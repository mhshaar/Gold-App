from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn, urllib.request, json, math, time, requests, os, zlib, struct

app = FastAPI(title="TRADLI Gold Pro", version="13.1")

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "07f70213d88f4a058afb2f9d1d765f61")

_cache = {}
_cache_time = {}
_icon_cache = {}


# ---------------------------------------------------------------- Icons (real PNG 192 / 512)
def make_icon_png(size):
    if size in _icon_cache:
        return _icon_cache[size]
    bg, gold, orange = (11, 14, 20), (227, 179, 65), (255, 136, 0)
    cx = cy = size / 2
    r_out, r_in = size * 0.30, size * 0.22
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            if d2 <= r_in * r_in:
                rows.extend(orange)
            elif d2 <= r_out * r_out:
                rows.extend(gold)
            else:
                rows.extend(bg)

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
           + chunk(b"IEND", b""))
    _icon_cache[size] = png
    return png


@app.get("/icon-192.png")
def icon192():
    return Response(content=make_icon_png(192), media_type="image/png")


@app.get("/icon-512.png")
def icon512():
    return Response(content=make_icon_png(512), media_type="image/png")


@app.get("/apple-touch-icon.png")
def apple_icon():
    return Response(content=make_icon_png(192), media_type="image/png")


@app.get("/manifest.json")
def get_manifest():
    return JSONResponse(
        content={
            "id": "/",
            "name": "TRADLI Gold Pro",
            "short_name": "TRADLI",
            "description": "Gold AI Analyzer",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "lang": "ar",
            "dir": "rtl",
            "background_color": "#0b0e14",
            "theme_color": "#ff8800",
            "icons": [
                {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
                {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}
            ]
        },
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
def get_sw():
    js = """
self.addEventListener('install', function(e){ self.skipWaiting(); });
self.addEventListener('activate', function(e){ e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function(event){
  event.respondWith(fetch(event.request).catch(function(){ return new Response('offline', {status: 503}); }));
});
"""
    return Response(content=js, media_type="application/javascript",
                    headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


# ---------------------------------------------------------------- Data
def get_live_gold_price():
    try:
        req = urllib.request.Request("https://api.gold-api.com/price/XAU", headers={"User-Agent": "Mozilla/5.0"})
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
        if "values" not in data: return None
        candles = []
        for row in reversed(data["values"]):
            candles.append({
                "time": row["datetime"], "open": float(row["open"]),
                "high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"])
            })
        _cache[cache_key] = candles
        _cache_time[cache_key] = now
        return candles
    except Exception:
        return None


def fetch_candles(interval, limit=200, base_price=None):
    c = _fetch_twelvedata(interval, limit)
    if c and len(c) >= 60: return c, "live"
    return [], "synthetic"


# ---------------------------------------------------------------- Indicators
def ema(values, period):
    if not values: return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]: out.append(v * k + out[-1] * (1 - k))
    return out


def sma(values, period):
    if len(values) < period: return values[-1]
    return sum(values[-period:]) / period


def rsi(closes, period=14):
    if len(closes) < period + 1: return [50.0] * len(closes)
    out = [50.0] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag, al = sum(gains[:period]) / period, sum(losses[:period]) / period
    out[period] = 100 - 100 / (1 + (ag / al if al else 100))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100 - 100 / (1 + (ag / al if al else 100))
    return out


def macd(closes, f=12, s=26, sig=9):
    if len(closes) < s + sig: return [0.0] * len(closes), [0.0] * len(closes), [0.0] * len(closes)
    ef, es = ema(closes, f), ema(closes, s)
    line = [a - b for a, b in zip(ef, es)]
    signal = ema(line, sig)
    hist = [a - b for a, b in zip(line, signal)]
    return line, signal, hist


def atr(candles, period=14):
    if len(candles) < 2: return 1.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    window = trs[-period:] if len(trs) >= period else trs
    return sum(window) / len(window)


def get_support_resistance(candles):
    recent = candles[-50:]
    resistance = max(c["high"] for c in recent)
    support = min(c["low"] for c in recent)
    return round(support, 2), round(resistance, 2)


def analyze_timeframe(candles, tf_name):
    if not candles:
        return {
            "score": 0, "atr": 0.0, "ema200": 0.0, "last_price": 0.0,
            "support": 0.0, "resistance": 0.0, "rsi": 50.0,
            "sweep_buy": False, "sweep_sell": False, "wick_buy": False, "wick_sell": False,
        }

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    last = candles[-1]
    curr_range = last["high"] - last["low"]
    if curr_range == 0: curr_range = 0.0001

    e9, e21, e50 = (ema(closes, p)[-1] for p in (9, 21, 50))
    e200 = ema(closes, 200)[-1] if len(closes) >= 200 else closes[0]
    r = rsi(closes)[-1]
    line, sig, hist = macd(closes)
    a = atr(candles)
    sup, res = get_support_resistance(candles)

    upper_band = sma(highs, 20) + (a * 0.5)
    lower_band = sma(lows, 20) - (a * 0.5)

    lower_wick = min(last["open"], last["close"]) - last["low"]
    upper_wick = last["high"] - max(last["open"], last["close"])
    wick_ratio_buy = lower_wick / curr_range
    wick_ratio_sell = upper_wick / curr_range

    recent_lows = lows[-21:-1]
    recent_highs = highs[-21:-1]
    prev_min = min(recent_lows)
    prev_max = max(recent_highs)

    sweep_buy = (last["low"] < prev_min) and (last["close"] > prev_min)
    sweep_sell = (last["high"] > prev_max) and (last["close"] < prev_max)

    score = 0

    if e9 > e21:
        score += 1
    elif e9 < e21:
        score -= 1
    if e21 > e50:
        score += 1
    elif e21 < e50:
        score -= 1
    if hist[-1] > 0:
        score += 1
    elif hist[-1] < 0:
        score -= 1

    if last["low"] <= lower_band and wick_ratio_buy > 0.25:
        score += 2
    elif last["high"] >= upper_band and wick_ratio_sell > 0.25:
        score -= 2

    if sweep_buy:
        score += 2
    elif sweep_sell:
        score -= 2

    if r > 55:
        score += 1
    elif r < 45:
        score -= 1

    return {
        "score": score, "atr": round(a, 2), "ema200": round(e200, 2),
        "last_price": round(last["close"], 2), "support": sup, "resistance": res,
        "rsi": round(r, 1), "sweep_buy": sweep_buy, "sweep_sell": sweep_sell,
        "wick_buy": wick_ratio_buy > 0.25, "wick_sell": wick_ratio_sell > 0.25,
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
    support = ref.get("support", price - 10)
    resistance = ref.get("resistance", price + 10)

    trend_txt = "صاعد" if price > ema200 else "هابط"
    analysis_txt = f"الاتجاه العام للذهب على الإطار الزمني (H1) هو <b>{trend_txt}</b>، حيث يتداول السعر {'فوق' if price > ema200 else 'تحت'} المتوسط المتحرك EMA 200. "

    if composite >= 0.6:
        analysis_txt += "مؤشرات الزخم تدعم المشترين، حيث تقع المتوسطات (EMA) في ترتيب إيجابي، مع تأكيد من MACD. "
    elif composite <= -0.6:
        analysis_txt += "مؤشرات الزخم تدعم البائعين، حيث تقع المتوسطات (EMA) في ترتيب سلبي، مع تأكيد هبوطي من MACD. "
    else:
        analysis_txt += "مؤشرات الزخم متضاربة بين الأطر الزمنية، مما يشير إلى حالة من التذبذب وغياب اتجاه واضح. "

    analysis_txt += f"يقف مؤشر RSI عند {ref['rsi']} مما يدل على {'حيادية تميل للقوة' if ref['rsi'] > 50 else 'حيادية تميل للضعف'}. "

    if ref['sweep_buy']:
        analysis_txt += "علاوة على ذلك، أظهر السعر ارتداداً قوياً بعد مسح سيولة (Sweep) أسفل قاع سابق. "
    elif ref['sweep_sell']:
        analysis_txt += "علاوة على ذلك، أظهر السعر رفضاً قوياً بعد مسح سيولة (Sweep) أعلى قمة سابقة. "

    if ref['wick_buy']:
        analysis_txt += "ظهر ذيل شمعة سفلي طويل عند منطقة الطلب (Dark Band). "
    elif ref['wick_sell']:
        analysis_txt += "ظهر ذيل شمعة علوي طويل عند منطقة العرض (Dark Band). "

    signal = "WAIT"
    sl = tp1 = tp2 = tp3 = 0.0
    warning = ""
    trade_plan = ""

    if composite >= 0.6 and price > ema200:
        if resistance - price < (atr_val * 0.5):
            signal = "WAIT"
            warning = "السعر قريب من مقاومة قوية، يفضل الانتظار."
            analysis_txt += "ومع ذلك، السعر قريب جداً من مستوى مقاومة قوي مما يمنع الدخول في صفقة شراء."
        else:
            signal = "BUY"
            sl = round(price - 1.5 * atr_val, 2)
            tp1 = round(price + 1.5 * atr_val, 2)
            tp2 = round(price + 3.0 * atr_val, 2)
            tp3 = round(price + 4.5 * atr_val, 2)
            trade_plan = f"1️⃣ أغلق 50% عند TP1 ({tp1})\n2️⃣ انقل وقف الخسارة للدخول ({price})\n3️⃣ استخدم تريلينج ستوب بعد TP2 ({tp2})"
            analysis_txt += "السعر يبتعد بشكل جيد عن مستويات المقاومة، مما يمنح الصفقة مساحة للتحرك نحو الأهداف."

    elif composite <= -0.6 and price < ema200:
        if price - support < (atr_val * 0.5):
            signal = "WAIT"
            warning = "السعر قريب من دعم قوي، يفضل الانتظار."
            analysis_txt += "ومع ذلك، السعر قريب جداً من مستوى دعم قوي مما يمنع الدخول في صفقة بيع."
        else:
            signal = "SELL"
            sl = round(price + 1.5 * atr_val, 2)
            tp1 = round(price - 1.5 * atr_val, 2)
            tp2 = round(price - 3.0 * atr_val, 2)
            tp3 = round(price - 4.5 * atr_val, 2)
            trade_plan = f"1️⃣ أغلق 50% عند TP1 ({tp1})\n2️⃣ انقل وقف الخسارة للدخول ({price})\n3️⃣ استخدم تريلينج ستوب بعد TP2 ({tp2})"
            analysis_txt += "السعر يبتعد بشكل جيد عن مستويات الدعم، مما يمنح الصفقة مساحة للتحرك نحو الأهداف."
    else:
        analysis_txt += "بناءً على توافق المؤشرات الحالي، لا توجد فرصة تداول واضحة في هذه اللحظة، والانتظار هو القرار الأفضل."

    return {
        "signal": signal, "entry_price": round(price, 2), "stop_loss": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "data_source": data_source,
        "warning": warning, "support": support, "resistance": resistance,
        "trade_plan": trade_plan, "analysis_text": analysis_txt
    }


# ---------------------------------------------------------------- API
@app.get("/api/price")
def api_price():
    return {"price": get_live_gold_price()}


@app.get("/api/analyze")
def api_analyze():
    live = get_live_gold_price()
    per_tf = {}
    for t in ("H4", "H1", "M15", "M5"):
        candles, _ = fetch_candles(t, 250, live)
        per_tf[t] = analyze_timeframe(candles, t)
    return build_decision(per_tf, live, "live")


@app.get("/", response_class=HTMLResponse)
def read_root():
    return """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#ff8800">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="TRADLI">
<title>TRADLI Gold Pro</title>
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/png" href="/icon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<style>
body{background:#0b0e14;color:#fff;font-family:sans-serif;text-align:center;padding:20px;margin:0}
.box{max-width:420px;margin:auto;background:#161b22;border-radius:16px;padding:20px;box-shadow:0 4px 15px rgba(0,0,0,0.5)}
.btn{background:linear-gradient(90deg,#ff6b00,#ff8800);color:#fff;padding:12px;border:none;border-radius:8px;width:100%;font-size:16px;font-weight:bold;cursor:pointer;margin-bottom:10px}
.btn:active{transform:scale(0.98)}
#installBtn{display:none;background:linear-gradient(90deg,#238636,#2ea043)}
.card{display:none;margin-top:15px;background:#0d1117;padding:15px;border-radius:10px;text-align:right}
.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #21262d;font-size:14px}
.row:last-child{border-bottom:none}
.analysis-box{background:rgba(88,166,255,0.1);border-right:4px solid #58a6ff;padding:12px;border-radius:8px;margin-top:10px;margin-bottom:15px;font-size:13px;line-height:1.8;color:#c9d1d9;text-align:justify}
.warning-box{background:rgba(248,81,73,0.1);border:1px solid #f85149;padding:10px;border-radius:8px;margin-top:10px;font-size:13px;color:#f85149;display:none}
.plan-box{background:rgba(63,185,80,0.1);border:1px solid #3fb950;padding:12px;border-radius:8px;margin-top:10px;font-size:13px;color:#3fb950;display:none;line-height:1.8}
.calc-box{background:#161b22;padding:15px;border-radius:10px;margin-top:15px;text-align:right;border:1px solid #30363d}
.calc-box input{width:100%;padding:8px;margin:5px 0;border-radius:5px;border:1px solid #30363d;background:#0d1117;color:#fff;box-sizing:border-box}
.calc-box button{width:100%;padding:10px;margin-top:10px;background:#238636;color:#fff;border:none;border-radius:5px;cursor:pointer;font-weight:bold}
</style></head>
<body>
<div class="box">
  <h2>🏆 TRADLI Gold Pro</h2>
  <button class="btn" id="installBtn">📲 تثبيت التطبيق على الهاتف</button>
  <div style="margin-bottom:15px">السعر: <span id="livePrice" style="color:#e3b341">--</span></div>

  <button class="btn" onclick="startAnalysis()">📍 بدء التحليل الشامل</button>

  <div id="result" class="card">
    <div style="text-align:center;margin-bottom:10px"><span id="signal" style="background:#444;padding:6px 20px;border-radius:15px;font-weight:bold">--</span></div>

    <div id="analysisBox" class="analysis-box"></div>

    <div class="row"><span>الدخول:</span> <b id="entry">--</b></div>
    <div class="row"><span>وقف الخسارة:</span> <b style="color:#f85149" id="sl">--</b></div>
    <div class="row"><span>TP1:</span> <b style="color:#3fb950" id="tp1">--</b></div>
    <div class="row"><span>TP2:</span> <b style="color:#3fb950" id="tp2">--</b></div>
    <div class="row"><span>TP3:</span> <b style="color:#3fb950" id="tp3">--</b></div>
    <div class="row"><span>الدعم:</span> <b id="support" style="color:#58a6ff">--</b></div>
    <div class="row"><span>المقاومة:</span> <b id="resistance" style="color:#58a6ff">--</b></div>

    <div id="warningBox" class="warning-box"></div>
    <div id="tradePlan" class="plan-box"></div>

    <div class="calc-box" id="calcBox">
      <div style="font-weight:bold;margin-bottom:10px;color:#e3b341">🧮 حاسبة إدارة المخاطر</div>
      <input type="number" id="balance" placeholder="رصيد الحساب (مثال: 1000)">
      <input type="number" id="risk" placeholder="نسبة المخاطرة % (مثال: 1)">
      <button onclick="calcLot()">حساب حجم العقد (اللوت)</button>
      <div id="lotResult" style="margin-top:10px;font-weight:bold;color:#3fb950;text-align:center"></div>
    </div>
  </div>
</div>

<script>
let currentData = null;

if ('serviceWorker' in navigator) {
  window.addEventListener('load', function(){ navigator.serviceWorker.register('/sw.js', {scope:'/'}); });
}
let deferredPrompt = null;
window.addEventListener('beforeinstallprompt', function(e){
  e.preventDefault();
  deferredPrompt = e;
  document.getElementById('installBtn').style.display = 'block';
});
document.getElementById('installBtn').addEventListener('click', async function(){
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  await deferredPrompt.userChoice;
  deferredPrompt = null;
  this.style.display = 'none';
});
window.addEventListener('appinstalled', function(){ document.getElementById('installBtn').style.display = 'none'; });

async function updatePrice(){
  try{ let r=await fetch('/api/price'); let d=await r.json(); if(d.price) document.getElementById('livePrice').innerText='$'+d.price; }catch(e){}
}
updatePrice();
setInterval(updatePrice,10000);

async function startAnalysis(){
  let d;
  try{
    let r=await fetch('/api/analyze');
    if(!r.ok) throw new Error('HTTP '+r.status);
    d=await r.json();
  }catch(e){
    document.getElementById('analysisBox').innerHTML = "<b>⚠️ تعذر جلب التحليل.</b> حاول مرة أخرى بعد قليل. (" + e.message + ")";
    document.getElementById('result').style.display='block';
    return;
  }
  currentData = d;

  let s=document.getElementById('signal');
  s.innerText=d.signal==='BUY'?'شراء BUY':d.signal==='SELL'?'بيع SELL':'انتظار WAIT';
  s.style.background=d.signal==='BUY'?'#238636':d.signal==='SELL'?'#da3633':'#484f58';

  const f=(v)=>v?v:'--';
  document.getElementById('entry').innerText=f(d.entry_price);
  document.getElementById('sl').innerText=f(d.stop_loss);
  document.getElementById('tp1').innerText=f(d.tp1);
  document.getElementById('tp2').innerText=f(d.tp2);
  document.getElementById('tp3').innerText=f(d.tp3);
  document.getElementById('support').innerText=f(d.support);
  document.getElementById('resistance').innerText=f(d.resistance);

  document.getElementById('analysisBox').innerHTML = "<b>📝 التحليل الفني المفصل:</b><br>" + d.analysis_text;

  let wb = document.getElementById('warningBox');
  if (d.warning) { wb.style.display='block'; wb.innerText = "⚠️ " + d.warning; }
  else { wb.style.display='none'; }

  let tp = document.getElementById('tradePlan');
  if (d.trade_plan && d.signal !== 'WAIT') {
      tp.style.display='block';
      tp.innerHTML = "<b>📋 خطة إدارة الصفقة:</b><br>" + d.trade_plan.replace(/\\n/g, "<br>");
  } else {
      tp.style.display='none';
  }

  document.getElementById('calcBox').style.display = (d.signal !== 'WAIT') ? 'block' : 'none';
  document.getElementById('result').style.display='block';
}

function calcLot(){
  if(!currentData || currentData.signal === 'WAIT') return;
  let balance = parseFloat(document.getElementById('balance').value);
  let riskPct = parseFloat(document.getElementById('risk').value);
  if(!balance || !riskPct) return alert("أدخل الرصيد ونسبة المخاطرة");
  let riskAmount = (balance * riskPct) / 100;
  let distance = Math.abs(currentData.entry_price - currentData.stop_loss);
  if(distance === 0) return;
  let lotSize = riskAmount / (distance * 100);
  document.getElementById('lotResult').innerText = "حجم العقد المناسب: " + lotSize.toFixed(2) + " لوت";
}
</script></body></html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("gold_analyzer:app", host="0.0.0.0", port=port)
