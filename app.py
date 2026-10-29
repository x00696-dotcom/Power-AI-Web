import os
import re
import time
import threading
from difflib import SequenceMatcher
from pathlib import Path

import requests
from flask import Flask, send_file, jsonify, request

BASE = Path(__file__).resolve().parent
app = Flask(__name__)
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "PowerAI-2ndSource/1.5.1"})

NEXAR_TOKEN = {"value": None, "expires": 0.0}
TOKEN_LOCK = threading.Lock()
MOUSER_VALIDATION = {"at": 0.0, "valid": None, "message": ""}
MOUSER_VALIDATION_LOCK = threading.Lock()

STOPWORDS = {
    "the","and","with","for","from","this","that","into","power","management","integrated","circuit","ics",
    "device","devices","controller","controllers","series","package","packages","reel","cut","tape","tray"
}

KEY_SPEC_PATTERNS = [
    ("Input Voltage", r"input.*voltage|vin|maxinputvoltage|mininputvoltage"),
    ("Output Voltage", r"output.*voltage|vout|maxoutputvoltage|minoutputvoltage"),
    ("Output / Charge Current", r"output.*current|charge.*current|current.*output|maxoutputcurrent|continuous.*current"),
    ("Switching Frequency", r"switch.*freq|frequency"),
    ("Topology", r"topology|configuration|converter.*type"),
    ("Interface", r"interface|i2c|smbus|spi"),
    ("Battery Cells", r"cell|battery.*cell"),
    ("Package / Case", r"case.*package|package.*case|package|case"),
    ("Pin Count", r"number.*pins|pin.*count|numberofpins"),
    ("Operating Temp", r"operating.*temp|temperature.*range"),
]


def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _tokens(s):
    toks = re.findall(r"[A-Za-z0-9+\-]+", str(s or "").lower())
    return {t for t in toks if len(t) >= 3 and t not in STOPWORDS and not t.isdigit()}


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        raise RuntimeError(f"Remote provider returned HTTP {resp.status_code} with non-JSON body")


def _mouser_key():
    raw = os.environ.get("MOUSER_SEARCH_API_KEY", "").strip()
    # Be forgiving when users paste the whole KEY=value line or wrap the value in quotes.
    if raw.upper().startswith("MOUSER_SEARCH_API_KEY="):
        raw = raw.split("=", 1)[1].strip()
    raw = raw.strip().strip('"').strip("'").strip()
    return raw


def _friendly_mouser_error(msg):
    text = str(msg or "")
    if "invalid unique identifier" in text.lower():
        return ("Mouser Search API Key 無效。請確認 Render Environment 的 "
                "MOUSER_SEARCH_API_KEY 使用的是 Mouser『Search API』金鑰，而不是 Cart/Order API Key，"
                "並且 Value 不要包含引號或 MOUSER_SEARCH_API_KEY= 前綴。")
    return text


def validate_mouser_key(force=False):
    key = _mouser_key()
    if not key:
        return {"configured": False, "valid": False, "message": "Not configured"}
    now = time.time()
    with MOUSER_VALIDATION_LOCK:
        if (not force and MOUSER_VALIDATION["valid"] is not None
                and now - MOUSER_VALIDATION["at"] < 600):
            return {"configured": True, "valid": MOUSER_VALIDATION["valid"],
                    "message": MOUSER_VALIDATION["message"]}
        try:
            # One tiny real Search API call verifies the key; cache 10 minutes to conserve quota.
            mouser_post("keyword", {"SearchByKeywordRequest": {
                "keyword": "BQ27510", "records": 1, "startingRecord": 0, "searchOptions": "None"
            }})
            valid, message = True, "Search API key validated"
        except Exception as e:
            valid, message = False, _friendly_mouser_error(str(e))
        MOUSER_VALIDATION.update({"at": now, "valid": valid, "message": message})
        return {"configured": True, "valid": valid, "message": message}


def _nexar_creds():
    return os.environ.get("NEXAR_CLIENT_ID", "").strip(), os.environ.get("NEXAR_CLIENT_SECRET", "").strip()


def provider_status():
    cid, sec = _nexar_creds()
    return {"nexar": bool(cid and sec), "mouser": bool(_mouser_key())}


def mouser_post(path, body):
    key = _mouser_key()
    if not key:
        raise RuntimeError("Mouser API is not configured")
    url = f"https://api.mouser.com/api/v1/search/{path}"
    r = HTTP.post(url, params={"apiKey": key}, json=body, timeout=25)
    data = _safe_json(r)
    if not r.ok:
        errs = data.get("Errors") or []
        msg = "; ".join(str(x.get("Message") or x) for x in errs) if isinstance(errs, list) else str(errs)
        raise RuntimeError(f"Mouser HTTP {r.status_code}: {msg or 'request failed'}")
    errs = data.get("Errors") or []
    if errs:
        msg = "; ".join(str(x.get("Message") or x) for x in errs if x)
        if msg:
            raise RuntimeError("Mouser: " + msg)
    return data


def mouser_part_search(mpn):
    data = mouser_post("partnumber", {"SearchByPartRequest": {"mouserPartNumber": mpn, "partSearchOptions": "None"}})
    return (data.get("SearchResults") or {}).get("Parts") or []


def mouser_batch(parts):
    vals = [p for p in parts if p][:10]
    if not vals:
        return []
    data = mouser_post("partnumber", {"SearchByPartRequest": {"mouserPartNumber": "|".join(vals), "partSearchOptions": "None"}})
    return (data.get("SearchResults") or {}).get("Parts") or []


def mouser_keyword(keyword, records=50):
    data = mouser_post("keyword", {"SearchByKeywordRequest": {"keyword": keyword, "records": min(50, max(1, records)), "startingRecord": 0, "searchOptions": "None"}})
    return (data.get("SearchResults") or {}).get("Parts") or []


def _mouser_attrs(part):
    out = {}
    for a in part.get("ProductAttributes") or []:
        k = str(a.get("AttributeName") or "").strip()
        v = str(a.get("AttributeValue") or "").strip()
        if k and v:
            out.setdefault(k, v)
    return out


def _price_from_mouser(part):
    vals = []
    for p in part.get("PriceBreaks") or []:
        q = p.get("Quantity")
        price = p.get("Price")
        if price:
            vals.append(f"{price} @ {q or '?'}")
        if len(vals) >= 2:
            break
    return " / ".join(vals)


def _package_from_attrs(attrs, packaging=""):
    for k, v in attrs.items():
        if re.search(r"case.*package|package.*case|package / case", k, re.I):
            return v
    return packaging or ""


def mouser_to_part(p):
    attrs = _mouser_attrs(p)
    key_specs = []
    for label, pat in KEY_SPEC_PATTERNS:
        for k, v in attrs.items():
            if re.search(pat, k.replace(" ", ""), re.I) or re.search(pat, k, re.I):
                key_specs.append({"name": label, "value": v})
                break
        if len(key_specs) >= 7:
            break
    return {
        "mpn": p.get("ManufacturerPartNumber") or "",
        "manufacturer": p.get("Manufacturer") or "",
        "description": p.get("Description") or "",
        "category": p.get("Category") or p.get("ProductCategory") or "",
        "datasheet": p.get("DataSheetUrl") or "",
        "product_url": p.get("ProductDetailUrl") or "",
        "lifecycle": p.get("LifecycleStatus") or "",
        "stock": p.get("Availability") or "",
        "price": _price_from_mouser(p),
        "package": _package_from_attrs(attrs, p.get("Packaging") or ""),
        "key_specs": key_specs,
        "attrs": attrs,
        "providers": ["Mouser"],
    }


def _get_nexar_token():
    cid, sec = _nexar_creds()
    if not (cid and sec):
        raise RuntimeError("Nexar API is not configured")
    now = time.time()
    with TOKEN_LOCK:
        if NEXAR_TOKEN["value"] and NEXAR_TOKEN["expires"] > now + 120:
            return NEXAR_TOKEN["value"]
        r = HTTP.post("https://identity.nexar.com/connect/token", data={
            "grant_type": "client_credentials", "client_id": cid, "client_secret": sec, "scope": "supply.domain"
        }, timeout=20)
        data = _safe_json(r)
        if not r.ok or not data.get("access_token"):
            raise RuntimeError("Nexar authorization failed: " + str(data.get("error_description") or data.get("error") or r.status_code))
        NEXAR_TOKEN["value"] = data["access_token"]
        NEXAR_TOKEN["expires"] = now + int(data.get("expires_in") or 3600)
        return NEXAR_TOKEN["value"]


def nexar_graphql(query, variables):
    token = _get_nexar_token()
    r = HTTP.post("https://api.nexar.com/graphql/", json={"query": query, "variables": variables}, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    data = _safe_json(r)
    if not r.ok:
        raise RuntimeError(f"Nexar HTTP {r.status_code}")
    if data.get("errors"):
        raise RuntimeError("Nexar: " + "; ".join(str(x.get("message") or x) for x in data["errors"]))
    return data.get("data") or {}


NEXAR_RICH_QUERY = r'''query PowerAI($q:String!){
  supSearchMpn(q:$q, limit:5){
    hits
    results{
      description
      part{
        id name mpn octopartUrl totalAvail
        manufacturer{name homepageUrl}
        category{name}
        medianPrice1000{quantity price currency convertedPrice convertedCurrency}
        specs{attribute{name shortname} displayValue}
        similarParts{name mpn octopartUrl}
      }
    }
  }
}'''

NEXAR_BASIC_QUERY = r'''query PowerAI($q:String!){
  supSearchMpn(q:$q, limit:5){
    hits
    results{
      description
      part{
        id name mpn octopartUrl totalAvail
        manufacturer{name homepageUrl}
        category{name}
        similarParts{name mpn octopartUrl}
      }
    }
  }
}'''


def _nexar_spec_map(specs):
    out = {}
    for s in specs or []:
        a = s.get("attribute") or {}
        k = a.get("shortname") or a.get("name") or ""
        v = s.get("displayValue") or ""
        if k and v:
            out[str(k)] = str(v)
    return out


def _key_specs_from_map(specmap):
    out = []
    used = set()
    for label, pat in KEY_SPEC_PATTERNS:
        for k, v in specmap.items():
            kk = re.sub(r"[^a-z0-9]", "", k.lower())
            if re.search(pat, k, re.I) or re.search(pat, kk, re.I):
                if label not in used:
                    out.append({"name": label, "value": v})
                    used.add(label)
                break
        if len(out) >= 7:
            break
    return out


def _package_from_specmap(specmap):
    for k, v in specmap.items():
        if re.search(r"case.*package|package.*case|package|case", k, re.I):
            return v
    return ""


def nexar_lookup(mpn):
    try:
        data = nexar_graphql(NEXAR_RICH_QUERY, {"q": mpn})
        rich = True
    except Exception as e:
        # Some Nexar plans do not include tech specs. Similar-parts search still works with the basic query.
        if "spec" not in str(e).lower() and "unauthorized" not in str(e).lower() and "access" not in str(e).lower():
            raise
        data = nexar_graphql(NEXAR_BASIC_QUERY, {"q": mpn})
        rich = False
    rs = ((data.get("supSearchMpn") or {}).get("results") or [])
    if not rs:
        return None, [], rich
    wanted = _norm(mpn)
    row = next((x for x in rs if _norm((x.get("part") or {}).get("mpn")) == wanted), rs[0])
    p = row.get("part") or {}
    specmap = _nexar_spec_map(p.get("specs"))
    med = p.get("medianPrice1000") or {}
    price = ""
    if med.get("convertedPrice") is not None:
        price = f"{med.get('convertedPrice')} {med.get('convertedCurrency') or ''} @1000".strip()
    elif med.get("price") is not None:
        price = f"{med.get('price')} {med.get('currency') or ''} @1000".strip()
    source = {
        "mpn": p.get("mpn") or mpn,
        "manufacturer": (p.get("manufacturer") or {}).get("name") or "",
        "description": row.get("description") or p.get("name") or "",
        "category": (p.get("category") or {}).get("name") or "",
        "datasheet": "",
        "product_url": p.get("octopartUrl") or "",
        "lifecycle": "",
        "stock": str(p.get("totalAvail") or ""),
        "price": price,
        "package": _package_from_specmap(specmap),
        "key_specs": _key_specs_from_map(specmap),
        "attrs": specmap,
        "providers": ["Nexar/Octopart"],
    }
    sims = []
    for sp in p.get("similarParts") or []:
        smpn = sp.get("mpn") or ""
        if smpn and _norm(smpn) != wanted:
            sims.append({"mpn": smpn, "name": sp.get("name") or "", "product_url": sp.get("octopartUrl") or ""})
    return source, sims, rich


def _pick_exact_mouser(parts, mpn):
    wanted = _norm(mpn)
    if not parts:
        return None
    return next((x for x in parts if _norm(x.get("ManufacturerPartNumber")) == wanted), parts[0])


def _merge(base, extra):
    if not base:
        return dict(extra)
    out = dict(base)
    for k in ("manufacturer","description","category","datasheet","product_url","lifecycle","stock","price","package"):
        if not out.get(k) and extra.get(k):
            out[k] = extra[k]
    # Prefer richer key spec list; merge by name.
    merged = {x.get("name"): x for x in out.get("key_specs") or [] if x.get("name")}
    for x in extra.get("key_specs") or []:
        if x.get("name") and x.get("name") not in merged:
            merged[x["name"]] = x
    out["key_specs"] = list(merged.values())
    attrs = dict(out.get("attrs") or {})
    attrs.update(extra.get("attrs") or {})
    out["attrs"] = attrs
    prov = list(dict.fromkeys((out.get("providers") or []) + (extra.get("providers") or [])))
    out["providers"] = prov
    return out


def _spec_similarity(source, cand):
    score = 0.0
    reasons = []
    if source.get("category") and cand.get("category"):
        if source["category"].strip().lower() == cand["category"].strip().lower():
            score += 22; reasons.append("same category")
        elif SequenceMatcher(None, source["category"].lower(), cand["category"].lower()).ratio() >= 0.65:
            score += 12; reasons.append("similar category")
    st, ct = _tokens(source.get("description")), _tokens(cand.get("description"))
    if st and ct:
        j = len(st & ct) / max(1, len(st | ct))
        score += min(28, j * 45)
        if j >= .35: reasons.append("description/function overlap")
    sa = {re.sub(r"[^a-z0-9]","",k.lower()): str(v).strip().lower() for k,v in (source.get("attrs") or {}).items()}
    ca = {re.sub(r"[^a-z0-9]","",k.lower()): str(v).strip().lower() for k,v in (cand.get("attrs") or {}).items()}
    common = set(sa) & set(ca)
    exact = 0
    for k in common:
        if sa[k] and ca[k] and sa[k] == ca[k]:
            exact += 1
    if exact:
        score += min(25, exact * 4); reasons.append(f"{exact} matching attributes")
    sp, cp = source.get("package",""), cand.get("package","")
    package_same = bool(sp and cp and _norm(sp) == _norm(cp))
    if package_same:
        score += 12; reasons.append("same package text")
    if source.get("manufacturer") and cand.get("manufacturer") and source["manufacturer"].lower() != cand["manufacturer"].lower():
        score += 5; reasons.append("different manufacturer")
    if cand.get("lifecycle") and re.search(r"active|production|new", cand["lifecycle"], re.I):
        score += 4
    return min(99, score), reasons, package_same


def _classify(score, source, cand, package_same, from_similar=False):
    # Nexar similarParts is an explicit similarity signal, but still not proof of pin compatibility.
    if from_similar:
        score = max(score, 72)
    if package_same and score >= 82:
        typ = "Strong Candidate"
    elif score >= 68:
        typ = "Functional Alternative"
    else:
        typ = "Similar Candidate"
    same_mfr = source.get("manufacturer") and cand.get("manufacturer") and source["manufacturer"].lower() == cand["manufacturer"].lower()
    if same_mfr:
        typ += " · Same Mfr"
    return min(99, score), typ


def _candidate_result(source, cand, score, typ, package_same, reasons):
    bits = []
    if source.get("manufacturer") and cand.get("manufacturer") and source["manufacturer"].lower() != cand["manufacturer"].lower():
        bits.append("不同廠牌，可作 2nd-source 優先評估")
    elif source.get("manufacturer") and cand.get("manufacturer"):
        bits.append("同廠牌替代料，不能取代真正 multi-source qualification")
    if package_same:
        bits.append("Package 文字相同，但 Pinout 尚未驗證")
    else:
        bits.append("Package/Pinout 需重新確認")
    if reasons:
        bits.append("比對依據：" + ", ".join(reasons[:3]))
    return "；".join(bits) + "。"


def discover_mouser(source_part):
    desc = source_part.get("description") or ""
    cat = source_part.get("category") or ""
    toks = [t for t in re.findall(r"[A-Za-z0-9+\-]+", desc) if len(t) >= 4 and t.lower() not in STOPWORDS]
    keyword = " ".join(([cat] if cat else []) + toks[:8])
    if not keyword.strip():
        keyword = source_part.get("mpn") or ""
    raw = mouser_keyword(keyword[:180], 50)
    out = []
    wanted = _norm(source_part.get("mpn"))
    for p in raw:
        if _norm(p.get("ManufacturerPartNumber")) == wanted:
            continue
        cp = mouser_to_part(p)
        score, reasons, pkg_same = _spec_similarity(source_part, cp)
        # Keyword fallback should be conservative.
        if source_part.get("category") and cp.get("category") and source_part["category"].lower() != cp["category"].lower() and score < 45:
            continue
        score, typ = _classify(score, source_part, cp, pkg_same, False)
        cp.update({"score": score, "type": typ, "pinout": "Not Verified", "result": _candidate_result(source_part, cp, score, typ, pkg_same, reasons), "_similar": False})
        out.append(cp)
    out.sort(key=lambda x: x.get("score",0), reverse=True)
    return out[:8]


def search_crossref(mpn):
    status = provider_status()
    if not any(status.values()):
        raise RuntimeError("尚未設定線上資料源。請在 Render Environment 至少加入 NEXAR_CLIENT_ID + NEXAR_CLIENT_SECRET，或 MOUSER_SEARCH_API_KEY。")

    source = None
    source_providers = []
    candidates = []
    nexar_sims = []
    nexar_rich = False
    errors = []

    if status["nexar"]:
        try:
            source, nexar_sims, nexar_rich = nexar_lookup(mpn)
            if source:
                source_providers.append("Nexar/Octopart")
        except Exception as e:
            errors.append(str(e))

    # Mouser identifies source if Nexar was unavailable, and enriches source if both are configured.
    if status["mouser"]:
        try:
            raw = mouser_part_search(mpn)
            exact = _pick_exact_mouser(raw, mpn)
            if exact:
                msrc = mouser_to_part(exact)
                source = _merge(source, msrc) if source else msrc
                if "Mouser" not in source_providers: source_providers.append("Mouser")
        except Exception as e:
            errors.append(_friendly_mouser_error(str(e)))

    if not source:
        raise RuntimeError("找不到 Original IC：" + mpn + ("；" + "；".join(errors) if errors else ""))

    # Nexar similarParts is the preferred candidate source.
    if nexar_sims:
        detail_map = {}
        for sp in nexar_sims[:6]:
            try:
                cp, _, _ = nexar_lookup(sp["mpn"])
                if cp:
                    detail_map[_norm(sp["mpn"])] = cp
            except Exception:
                pass
        for sp in nexar_sims[:6]:
            cp = detail_map.get(_norm(sp["mpn"])) or {"mpn": sp["mpn"], "description": sp.get("name") or "", "product_url": sp.get("product_url") or "", "providers": ["Nexar/Octopart"], "key_specs": [], "attrs": {}}
            score, reasons, pkg_same = _spec_similarity(source, cp)
            score += 18  # explicit similarParts signal
            score, typ = _classify(score, source, cp, pkg_same, True)
            cp.update({"score": score, "type": typ, "pinout": "Not Verified", "result": _candidate_result(source, cp, score, typ, pkg_same, ["Nexar similarParts"] + reasons), "_similar": True})
            candidates.append(cp)

    # Mouser enrichment in one batch call where possible.
    if status["mouser"] and candidates:
        try:
            raw = mouser_batch([c["mpn"] for c in candidates])
            by = {}
            for p in raw:
                key = _norm(p.get("ManufacturerPartNumber"))
                if key:
                    by[key] = mouser_to_part(p)
            enriched = []
            for c in candidates:
                merged = _merge(c, by.get(_norm(c["mpn"]), {}))
                score, reasons, pkg_same = _spec_similarity(source, merged)
                score += 18 if c.get("_similar") else 0
                score, typ = _classify(score, source, merged, pkg_same, c.get("_similar",False))
                merged.update({"score":score,"type":typ,"pinout":"Not Verified","result":_candidate_result(source, merged, score, typ, pkg_same, (["Nexar similarParts"] if c.get("_similar") else []) + reasons)})
                enriched.append(merged)
            candidates = enriched
        except Exception as e:
            errors.append(_friendly_mouser_error(str(e)))

    # If no Nexar candidates, or fewer than five, supplement with Mouser discovery.
    if status["mouser"] and len(candidates) < 5:
        try:
            more = discover_mouser(source)
            existing = {_norm(c.get("mpn")) for c in candidates} | {_norm(source.get("mpn"))}
            for c in more:
                if _norm(c.get("mpn")) not in existing:
                    candidates.append(c); existing.add(_norm(c.get("mpn")))
                if len(candidates) >= 8:
                    break
        except Exception as e:
            errors.append(_friendly_mouser_error(str(e)))

    # Re-score and keep sane public output.
    uniq = {}
    for c in candidates:
        k = _norm(c.get("mpn"))
        if not k or k == _norm(source.get("mpn")):
            continue
        if k not in uniq or c.get("score",0) > uniq[k].get("score",0):
            uniq[k] = c
    candidates = sorted(uniq.values(), key=lambda x: x.get("score",0), reverse=True)[:8]

    for obj in [source] + candidates:
        obj.pop("attrs", None)
        obj.pop("_similar", None)
        obj["providers"] = list(dict.fromkeys(obj.get("providers") or []))

    mode = "Nexar Similar Parts + Mouser enrichment" if nexar_sims and status["mouser"] else ("Nexar Similar Parts" if nexar_sims else "Mouser keyword/spec discovery")
    return {
        "source": source,
        "candidates": candidates,
        "providers": status,
        "source_providers": source_providers,
        "search_mode": mode,
        "warnings": errors[:3],
        "pinout_policy": "Never claim P2P without datasheet-level pinout verification"
    }


@app.after_request
def add_local_portable_cors(resp):
    # Allow the trusted Local Portable page (file:// sends Origin: null) to call only this bridge.
    origin = request.headers.get("Origin", "")
    if origin == "null" or origin.endswith(".onrender.com"):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


@app.get("/")
def index():
    return send_file(BASE / "app.html")


@app.get("/PowerAI_EXP_Export_SOP.pdf")
def exp_export_sop():
    return send_file(BASE / "PowerAI_EXP_Export_SOP.pdf", as_attachment=True, download_name="PowerAI_EXP_Export_SOP.pdf", mimetype="application/pdf")


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "Power AI V15 Online 2nd Source Bridge", "providers": provider_status()})


@app.get("/api/provider-status")
def api_provider_status():
    p = provider_status()
    validation = {}
    mouser_ok = False
    if p.get("mouser"):
        validation["mouser"] = validate_mouser_key()
        mouser_ok = bool(validation["mouser"].get("valid"))
    else:
        validation["mouser"] = {"configured": False, "valid": False, "message": "Not configured"}
    # Nexar remains configuration-based here; actual authorization is checked on search.
    validation["nexar"] = {"configured": bool(p.get("nexar")), "valid": None,
                           "message": "Configured; authorization is checked on search" if p.get("nexar") else "Not configured"}
    online = mouser_ok or bool(p.get("nexar"))
    return jsonify({"ok": True, "online": online, "providers": p, "validation": validation})


@app.post("/api/crossref")
def api_crossref():
    body = request.get_json(silent=True) or {}
    mpn = str(body.get("mpn") or "").strip()
    if len(mpn) < 2 or len(mpn) > 120:
        return jsonify({"error": "請輸入有效的 Manufacturer Part Number"}), 400
    if not re.fullmatch(r"[A-Za-z0-9_+./()#\- ]+", mpn):
        return jsonify({"error": "料號包含不支援的字元"}), 400
    try:
        return jsonify(search_crossref(mpn))
    except RuntimeError as e:
        msg = str(e)
        code = 503 if "尚未設定線上資料源" in msg else 502
        return jsonify({"error": msg, "providers": provider_status()}), code
    except Exception as e:
        return jsonify({"error": "Online search unexpected error: " + str(e), "providers": provider_status()}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
