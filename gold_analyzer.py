"""
Gold AI Analyzer v6.1 — PWA Enabled
"""
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import uvicorn, urllib.request, json, math, random, time, requests, os
from datetime import datetime, timedelta

app = FastAPI(title="Gold AI Analyzer v6.1", version="6.1")

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "07f70213d88f4a058afb2f9d1d765f61")

_cache = {}
_cache_time = {}

def get_live_gold_price():
    try:
        req = urllib.request.Request(
            "https://api.gold-api.com/price/XAU",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return round(float(json.loads(r.read().decode())["price"]), 2)
    except Exception:
        pass
    return None

def _fetch_twelvedata(interval, limit=200):
    now = time.time()
    tf_map = {"M5": "5min", "M15": "15min", "H1": "1h", "H4": "4h"}
    td_interval = tf_map.get(interval, "15min")

    cache_key = f"{td_interval}_{limit}"
    if cache_key in _cache and (now - _cache_time.get(cache_key, 0)) < 60:
        return _cache[cache_key]

    url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={td_interval}&outputsize={limit}&apikey={TWELVE_DATA_API_KEY}"

    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        if "values" not in data:
            return None

        candles = []
        for row in reversed(data["values"]):
            candles.append({
                "time": row["datetime"],
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"])
            })

        _cache[cache_key] = candles
        _cache_time[cache_key] = now
        return candles
    except Exception:
        return None

def _synthetic(interval, limit, base_price):
    base_price = base_price or 2000.0
    step = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400}.get(interval, 900)
    rnd = random.Random(int(time.time() // 60) + abs(hash(interval)) % 99991)
    vol = base_price * 0.002 * math.sqrt(step / 900)
    path = [0.0]
    for _ in range(limit - 1): path.append(path[-1] + rnd.gauss(0, vol))
    n = len(path)
    for i in range(n): path[i] -= path[-1] * (i / max(n - 1, 1))
    now = datetime.utcnow()
    out = []
    for i, d in enumerate(path):
        close = base_price + d
        op = close + rnd.gauss(0, vol * 0.4)
        hi = max(op, close) + abs(rnd.gauss(0, vol * 0.5))
        lo = min(op, close) - abs(rnd.gauss(0, vol * 0.5))
        ts = now - timedelta(seconds=step * (n - 1 - i))
        out.append({"time": ts.isoformat(), "open": round(op, 2), "high": round(hi, 2), "low": round(lo, 2), "close": round(close, 2)})
    out[-1]["close"] = round(base_price, 2)
    return out

def fetch_candles(interval, limit=200, base_price=None):
    c = _fetch_twelvedata(interval, limit)
    if c and len(c) >= 60:
        return c, "live"
    return _synthetic(interval, limit, base_price), "synthetic"

def ema(values, period):
    if not values: return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]: out.append(v * k + out[-1] * (1 - k))
    return out

def rsi(closes, period=14):
    if len(closes) < period + 1: return [50.0] * len(closes)
    out = [50.0] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0)); losses.append(max(-d, 0.0))
    ag, al = sum(gains[:period]) / period, sum(losses[:period]) / period
    out[period] = 100 - 100 / (1 + (ag / al if al else 100))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100 - 100 / (1 + (ag / al if al else 100))
    return out

def macd(closes, f=12, s=26, sig=9):
    if len(closes) < s + sig: return [0.0]*len(closes), [0.0]*len(closes), [0.0]*len(closes)
    ef, es = ema(closes, f), ema(closes, s)
    line = [a - b for a, b in zip(ef, es)]
    signal = ema(line, sig)
    hist = [a - b for a, b in zip(line, signal)]
    return line, signal, hist

def atr(candles, period=14):
    if len(candles) < 2: return 1.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    window = trs[-period:] if len(trs) >= period else trs
    return sum(window) / len(window)

def analyze_timeframe(candles, tf_name):
    closes = [c["close"] for c in candles]
    e9, e21, e50 = (ema(closes, p)[-1] for p in (9, 21, 50))
    e200 = ema(closes, 200)[-1] if len(closes) >= 200 else closes[0]
    r = rsi(closes)[-1]
    line, sig, hist = macd(closes)
    a = atr(candles)

    score = 0
    if e9 > e21: score += 1
    elif e9 < e21: score -= 1
    if e21 > e50: score += 1
    elif e21 < e50: score -= 1
    if r > 55: score += 1
    elif r < 45: score -= 1
    if hist[-1] > 0: score += 1
    elif hist[-1] < 0: score -= 1

    bias = "bullish" if score >= 2 else "bearish" if score <= -2 else "neutral"
    return {
        "timeframe": tf_name, "last_price": round(closes[-1], 2),
        "ema9": round(e9, 2), "ema21": round(e21, 2), "ema50": round(e50, 2),
        "ema200": round(e200, 2), "rsi": round(r, 2), "macd_hist": round(hist[-1], 4),
        "atr": round(a, 2), "bias": bias, "score": score
    }

def build_decision(per_tf, live_price, data_source):
    weights = {"H4": 2.0, "H1": 1.5, "M15": 1.0, "M5": 0.7}
    total = wsum = 0.0
    for tf, d in per_tf.items():
        w = weights.get(tf, 1.0)
        total += d["score"] * w
        wsum += w
    composite = total / wsum if wsum else 0.0

    ref = per_tf.get("H1") or list(per_tf.values())[0]
    atr_val = ref["atr"] if ref["atr"] > 0 else 5.0
    price = live_price or ref["last_price"]

    ema200 = ref.get("ema200", price)
    trend_up = price > ema200
    trend_down = price < ema200

    signal = "WAIT"
    confidence = 20
    sl = tp1 = tp2 = tp3 = 0.0

    if composite >= 0.6 and trend_up:
        signal = "BUY"
        sl  = round(price - 1.5 * atr_val, 2)
        tp1 = round(price + 1.5 * atr_val, 2)
        tp2 = round(price + 3.0 * atr_val, 2)
        tp3 = round(price + 4.5 * atr_val, 2)
        confidence = min(95, int(50 + composite * 25))
    elif composite <= -0.6 and trend_down:
        signal = "SELL"
        sl  = round(price + 1.5 * atr_val, 2)
        tp1 = round(price - 1.5 * atr_val, 2)
        tp2 = round(price - 3.0 * atr_val, 2)
        tp3 = round(price - 4.5 * atr_val, 2)
        confidence = min(95, int(50 + abs(composite) * 25))

    return {
        "symbol": "XAU/USD", "signal": signal, "confidence": confidence,
        "entry_price": round(price, 2), "stop_loss": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "atr_h1": round(atr_val, 2), "data_source": data_source,
    }

@app.get("/manifest.json")
def get_manifest():
    return {
        "name": "TRADLI Gold",
        "short_name": "TRADLI",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0b0e14",
        "theme_color": "#ff8800",
        "icons": [{"src": "https://cdn-icons-png.flaticon.com/512/2661/2661771.png", "sizes": "512x512", "type": "image/png"}]
    }

@app.get("/api/price")
def api_price():
    p = get_live_gold_price()
    return {"price": p, "source": "gold-api.com" if p else "unavailable"}

@app.get("/api/analyze")
def api_analyze(tf: str = "M15"):
    live = get_live_gold_price()
    per_tf = {}
    sources = set()
    for t in ("H4", "H1", "M15", "M5"):
        candles, src = fetch_candles(t, 250, live)
        sources.add(src)
        per_tf[t] = analyze_timeframe(candles, t)

    data_source = "live" if "live" in sources else "synthetic"
    return build_decision(per_tf, live, data_source)

@app.get("/", response_class=HTMLResponse)
def read_root():
    return HTML

HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="manifest" href="/manifest.json">
<title>TRADLI v6</title>
<style>
body{background:#0b0e14;color:#fff;font-family:sans-serif;text-align:center;padding:20px}
.container{max-width:420px;margin:auto;background:#161b22;border-radius:16px;padding:20px;box-shadow:0 4px 15px rgba(0,0,0,0.5)}
.btn{background:linear-gradient(90deg,#ff6b00,#ff8800);color:#fff;padding:12px;border:none;border-radius:8px;width:100%;font-size:16px;font-weight:bold;cursor:pointer;margin-bottom:10px}
.btn:active{transform:scale(0.98)}
.card{display:none;margin-top:15px;background:#0d1117;padding:15px;border-radius:10px;text-align:right;border:1px solid #30363d}
.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #21262d;font-size:14px}
.row:last-child{border-bottom:none}
h2{margin-top:0}
</style></head>
<body>
<div class="container">
  <h2>🏆 Gold AI Analyzer v6</h2>
  <div style="font-size:12px;color:#8b949e;margin-bottom:15px">Live Price: <span style="color:#e3b341;font-weight:bold" id="livePrice">--</span></div>
  
  <button class="btn" onclick="startAnalysis()">📍 بدء التحليل الذكي (مع فلتر الترند)</button>
  
  <div id="result" class="card">
    <div style="text-align:center;margin-bottom:15px"><span id="signalBadge" style="background:#444;padding:6px 20px;border-radius:20px;font-weight:bold;font-size:16px">--</span></div>
    <div class="row"><span>سعر الدخول:</span> <b id="entry" style="color:#e3b341">--</b></div>
    <div class="row"><span style="color:#f85149">وقف الخسارة (1.5 ATR):</span> <b style="color:#f85149" id="sl">--</b></div>
    <div class="row"><span style="color:#3fb950">TP1 (1R):</span> <b style="color:#3fb950" id="tp1">--</b></div>
    <div class="row"><span style="color:#3fb950">TP2 (2R):</span> <b style="color:#3fb950" id="tp2">--</b></div>
    <div class="row"><span style="color:#3fb950">TP3 (3R):</span> <b style="color:#3fb950" id="tp3">--</b></div>
    <div class="row"><span>مصدر البيانات:</span> <b id="src">--</b></div>
  </div>
</div>
<script>
setInterval(async()=>{
    try{
        let r=await fetch('/api/price');
        let d=await r.json();
        if(d.price) document.getElementById('livePrice').innerText='$'+d.price;
    }catch(e){}
},10000);

async function startAnalysis(){
  try{
      let r=await fetch('/api/analyze'); 
      let d=await r.json();
      let b=document.getElementById('signalBadge');
      
      b.innerText = d.signal==='BUY'?'شراء BUY':d.signal==='SELL'?'بيع SELL':'انتظار WAIT';
      b.style.background = d.signal==='BUY'?'#238636':d.signal==='SELL'?'#da3633':'#484f58';
      
      const fmt = (val) => (val && val !== 0) ? '$' + val : '--';
      
      document.getElementById('entry').innerText = fmt(d.entry_price);
      document.getElementById('sl').innerText = fmt(d.stop_loss);
      document.getElementById('tp1').innerText = fmt(d.tp1);
      document.getElementById('tp2').innerText = fmt(d.tp2);
      document.getElementById('tp3').innerText = fmt(d.tp3);
      document.getElementById('src').innerText = d.data_source==='live'?'Twelve Data ✅':'محاكاة ⚠️';
      
      document.getElementById('result').style.display='block';
  }catch(e){
      alert("حدث خطأ في الاتصال بالسيرفر.");
  }
}
</script></body></html>
"""

if __name__ == "__main__":
    uvicorn.run("gold_analyzer:app", host="0.0.0.0", port=8000)