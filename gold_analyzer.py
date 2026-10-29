from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import uvicorn, urllib.request, json, math, time, requests, os

app = FastAPI(title="Gold AI Analyzer Pro", version="11.0")

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "07f70213d88f4a058afb2f9d1d765f61")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ICON_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


@app.get("/icon.png")
def get_icon():
    import base64
    return Response(content=base64.b64decode(ICON_BASE64), media_type="image/png")


@app.get("/manifest.json")
def get_manifest():
    return {
        "name": "TRADLI Gold Pro", "short_name": "TRADLI", "start_url": "/",
        "display": "standalone", "background_color": "#0b0e14", "theme_color": "#ff8800",
        "icons": [{"src": "/icon.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/icon.png", "sizes": "512x512", "type": "image/png"}]
    }


@app.get("/sw.js")
def get_sw():
    return Response(content="self.addEventListener('fetch', function(event) {});", media_type="application/javascript")


_cache = {}
_cache_time = {}


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
        gains.append(max(d, 0.0));
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
    # نبحث عن أعلى وأدنى سعر في آخر 50 شمعة
    recent = candles[-50:]
    resistance = max(c["high"] for c in recent)
    support = min(c["low"] for c in recent)
    return round(support, 2), round(resistance, 2)


def analyze_timeframe(candles, tf_name):
    closes = [c["close"] for c in candles]
    e9, e21, e50 = (ema(closes, p)[-1] for p in (9, 21, 50))
    e200 = ema(closes, 200)[-1] if len(closes) >= 200 else closes[0]
    r = rsi(closes)[-1]
    line, sig, hist = macd(closes)
    a = atr(candles)
    sup, res = get_support_resistance(candles)

    score = 0
    reasons = []

    if e9 > e21:
        score += 1; reasons.append("زخم صاعد (EMA9>21)")
    elif e9 < e21:
        score -= 1; reasons.append("زخم هابط (EMA9<21)")

    if e21 > e50:
        score += 1; reasons.append("اتجاه صاعد (EMA21>50)")
    elif e21 < e50:
        score -= 1; reasons.append("اتجاه هابط (EMA21<50)")

    if r > 55:
        score += 1; reasons.append(f"RSI إيجابي ({r:.1f})")
    elif r < 45:
        score -= 1; reasons.append(f"RSI سلبي ({r:.1f})")

    if hist[-1] > 0:
        score += 1; reasons.append("MACD إيجابي")
    elif hist[-1] < 0:
        score -= 1; reasons.append("MACD سلبي")

    return {
        "score": score, "atr": round(a, 2), "ema200": round(e200, 2),
        "last_price": round(closes[-1], 2), "support": sup, "resistance": res, "reasons": reasons
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

    signal = "WAIT"
    sl = tp1 = tp2 = tp3 = 0.0
    reasons = ref.get("reasons", [])
    warning = ""

    # شرط الشراء (مع التأكد أن السعر ليس قريباً جداً من المقاومة)
    if composite >= 0.6 and price > ema200:
        if resistance - price < (atr_val * 0.5):
            signal = "WAIT"
            warning = "السعر قريب من مقاومة قوية، يفضل الانتظار."
        else:
            signal = "BUY"
            sl = round(price - 1.5 * atr_val, 2)
            tp1, tp2, tp3 = round(price + 1.5 * atr_val, 2), round(price + 3.0 * atr_val, 2), round(
                price + 4.5 * atr_val, 2)

    # شرط البيع (مع التأكد أن السعر ليس قريباً جداً من الدعم)
    elif composite <= -0.6 and price < ema200:
        if price - support < (atr_val * 0.5):
            signal = "WAIT"
            warning = "السعر قريب من دعم قوي، يفضل الانتظار."
        else:
            signal = "SELL"
            sl = round(price + 1.5 * atr_val, 2)
            tp1, tp2, tp3 = round(price - 1.5 * atr_val, 2), round(price - 3.0 * atr_val, 2), round(
                price - 4.5 * atr_val, 2)

    return {
        "signal": signal, "entry_price": round(price, 2), "stop_loss": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "data_source": data_source,
        "reasons": reasons, "warning": warning, "support": support, "resistance": resistance
    }


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


@app.get("/api/telegram")
def send_telegram(signal: str, entry: float, sl: float, tp1: float):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return {"status": "error", "message": "لم يتم إعداد التليجرام في Render"}

    msg = f"🏆 إشارة تداول الذهب 🏆\n\n"
    msg += f"الاتجاه: {signal}\n"
    msg += f"سعر الدخول: {entry}\n"
    msg += f"وقف الخسارة: {sl}\n"
    msg += f"الهدف الأول: {tp1}\n\n"
    msg += f"بالتوفيق! 🚀"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, json=payload, timeout=5)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/", response_class=HTMLResponse)
def read_root():
    return """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TRADLI Gold Pro</title>
<link rel="manifest" href="/manifest.json">
<style>
body{background:#0b0e14;color:#fff;font-family:sans-serif;text-align:center;padding:20px;margin:0}
.box{max-width:420px;margin:auto;background:#161b22;border-radius:16px;padding:20px;box-shadow:0 4px 15px rgba(0,0,0,0.5)}
.btn{background:linear-gradient(90deg,#ff6b00,#ff8800);color:#fff;padding:12px;border:none;border-radius:8px;width:100%;font-size:16px;font-weight:bold;cursor:pointer;margin-bottom:10px}
.btn:active{transform:scale(0.98)}
.card{display:none;margin-top:15px;background:#0d1117;padding:15px;border-radius:10px;text-align:right}
.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #21262d;font-size:14px}
.row:last-child{border-bottom:none}
.reasons{font-size:12px;color:#8b949e;margin-top:10px;text-align:right;line-height:1.6}
.warning-box{background:rgba(248,81,73,0.1);border:1px solid #f85149;padding:10px;border-radius:8px;margin-top:10px;font-size:13px;color:#f85149;display:none}
.calc-box{background:#161b22;padding:15px;border-radius:10px;margin-top:15px;text-align:right;border:1px solid #30363d}
.calc-box input{width:100%;padding:8px;margin:5px 0;border-radius:5px;border:1px solid #30363d;background:#0d1117;color:#fff;box-sizing:border-box}
.calc-box button{width:100%;padding:10px;margin-top:10px;background:#238636;color:#fff;border:none;border-radius:5px;cursor:pointer;font-weight:bold}
.telegram-btn{background:#0088cc;color:#fff;padding:10px;border:none;border-radius:8px;width:100%;font-size:14px;font-weight:bold;cursor:pointer;margin-top:15px;display:none}
</style></head>
<body>
<div class="box">
  <h2>🏆 TRADLI Gold Pro</h2>
  <div style="margin-bottom:15px">السعر: <span id="livePrice" style="color:#e3b341">--</span></div>

  <button class="btn" onclick="startAnalysis()">📍 بدء التحليل الشامل</button>

  <div id="result" class="card">
    <div style="text-align:center;margin-bottom:10px"><span id="signal" style="background:#444;padding:6px 20px;border-radius:15px;font-weight:bold">--</span></div>
    <div class="row"><span>الدخول:</span> <b id="entry">--</b></div>
    <div class="row"><span>وقف الخسارة:</span> <b style="color:#f85149" id="sl">--</b></div>
    <div class="row"><span>TP1:</span> <b style="color:#3fb950" id="tp1">--</b></div>
    <div class="row"><span>TP2:</span> <b style="color:#3fb950" id="tp2">--</b></div>
    <div class="row"><span>TP3:</span> <b style="color:#3fb950" id="tp3">--</b></div>
    <div class="row"><span>الدعم:</span> <b id="support" style="color:#58a6ff">--</b></div>
    <div class="row"><span>المقاومة:</span> <b id="resistance" style="color:#58a6ff">--</b></div>

    <div id="warningBox" class="warning-box"></div>
    <div id="reasonsList" class="reasons"></div>

    <!-- حاسبة المخاطر -->
    <div class="calc-box" id="calcBox">
      <div style="font-weight:bold;margin-bottom:10px;color:#e3b341">🧮 حاسبة إدارة المخاطر</div>
      <input type="number" id="balance" placeholder="رصيد الحساب (مثال: 1000)">
      <input type="number" id="risk" placeholder="نسبة المخاطرة % (مثال: 1)">
      <button onclick="calcLot()">حساب حجم العقد (اللوت)</button>
      <div id="lotResult" style="margin-top:10px;font-weight:bold;color:#3fb950;text-align:center"></div>
    </div>

    <button class="telegram-btn" id="telegramBtn" onclick="sendTelegram()">✈️ إرسال الإشارة إلى التليجرام</button>
  </div>
</div>

<script>
let currentData = null;

if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/sw.js'); }
setInterval(async()=>{ let r=await fetch('/api/price'); let d=await r.json(); if(d.price) document.getElementById('livePrice').innerText='$'+d.price; },10000);

async function startAnalysis(){
  let r=await fetch('/api/analyze'); let d=await r.json();
  currentData = d;

  let s=document.getElementById('signal');
  s.innerText=d.signal; s.style.background=d.signal==='BUY'?'#238636':d.signal==='SELL'?'#da3633':'#484f58';

  const f=(v)=>v?v:'--';
  document.getElementById('entry').innerText=f(d.entry_price);
  document.getElementById('sl').innerText=f(d.stop_loss);
  document.getElementById('tp1').innerText=f(d.tp1);
  document.getElementById('tp2').innerText=f(d.tp2);
  document.getElementById('tp3').innerText=f(d.tp3);
  document.getElementById('support').innerText=f(d.support);
  document.getElementById('resistance').innerText=f(d.resistance);

  // Reasons
  let rl = document.getElementById('reasonsList');
  rl.innerHTML = "<b>الأسباب:</b><br>• " + d.reasons.join("<br>• ");

  // Warning
  let wb = document.getElementById('warningBox');
  if (d.warning) { wb.style.display='block'; wb.innerText = "⚠️ " + d.warning; }
  else { wb.style.display='none'; }

  // Show/Hide buttons
  document.getElementById('telegramBtn').style.display = (d.signal !== 'WAIT') ? 'block' : 'none';
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

  // معادلة اللوت للذهب (1 لوت = 100 أونصة، أي حركة 1 دولار = 100 دولار ربح/خسارة)
  let lotSize = riskAmount / (distance * 100);
  document.getElementById('lotResult').innerText = "حجم العقد المناسب: " + lotSize.toFixed(2) + " لوت";
}

async function sendTelegram(){
  if(!currentData || currentData.signal === 'WAIT') return;
  let btn = document.getElementById('telegramBtn');
  btn.innerText = "جاري الإرسال...";
  let url = `/api/telegram?signal=${currentData.signal}&entry=${currentData.entry_price}&sl=${currentData.stop_loss}&tp1=${currentData.tp1}`;
  let r = await fetch(url);
  let d = await r.json();
  if(d.status === 'success'){
    btn.innerText = "✅ تم الإرسال بنجاح!";
    btn.style.background = "#238636";
  } else {
    alert("خطأ: " + d.message + "\n(تأكد من إعداد التوكن في Render)");
    btn.innerText = "✈️ إعادة المحاولة";
  }
}
</script></body></html>
"""


if __name__ == "__main__":
    uvicorn.run("gold_analyzer:app", host="0.0.0.0", port=8000)
