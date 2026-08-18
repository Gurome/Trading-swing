# -*- coding: utf-8 -*-
"""app.py — Semáforo Swing v2.
Pestañas: 🚦 Señal · 💼 Mis Posiciones (asesor de salida en vivo) ·
🏆 Comparador de instrumentos · 📐 Patrones chartistas.
Datos: Yahoo Finance (yfinance). Ejecutar: streamlit run app.py
"""
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

import core

st.set_page_config(page_title="Semáforo Swing", page_icon="🚦",
                   layout="centered")
st.markdown("""
<style>
.banner {border-radius:14px; padding:24px 10px; text-align:center;
         font-size:30px; font-weight:800; letter-spacing:1px;}
.verde {background:#34A853; color:white;}
.rojo {background:#EA4335; color:white;}
.amarillo {background:#FBBC04; color:#222;}
.naranja {background:#F57C00; color:white;}
.sub {text-align:center; color:#888; font-size:13px; margin-top:4px;}
div[data-testid="stMetricValue"] {font-size:20px;}
</style>
""", unsafe_allow_html=True)

TICKERS = ["BABA", "QQQ", "SPY", "MSFT", "AAPL", "GLD", "NVDA", "KWEB"]
POS_FILE = Path(__file__).parent / "positions.json"


# ---------------- datos ----------------
@st.cache_data(ttl=900, show_spinner="Descargando históricos…")
def fetch_daily(symbol: str) -> pd.DataFrame:
    df = yf.download(symbol, period="6y", interval="1d",
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


@st.cache_data(ttl=300)
def live_price(symbol: str) -> float:
    """Último precio disponible (intradía 5m con fallback al cierre diario)."""
    try:
        intra = yf.Ticker(symbol).history(period="1d", interval="5m")
        if len(intra):
            return float(intra["Close"].iloc[-1])
    except Exception:
        pass
    return float(fetch_daily(symbol)["Close"].iloc[-1])


def candles_with_ind(symbol: str, timeframe: str) -> pd.DataFrame:
    c = core.resample_ohlc(fetch_daily(symbol), timeframe)
    c = core.drop_open_candle(c, pd.Timestamp.now(tz="UTC"))
    return core.add_indicators(c)


# ---------------- posiciones: persistencia ----------------
def load_positions() -> list:
    if POS_FILE.exists():
        try:
            return json.loads(POS_FILE.read_text())
        except Exception:
            return []
    return []


def save_positions(positions: list):
    POS_FILE.write_text(json.dumps(positions, indent=2, default=str))


st.title("🚦 Semáforo Swing")
tab_sig, tab_pos, tab_cmp, tab_pat = st.tabs(
    ["🚦 Señal", "💼 Mis Posiciones", "🏆 Comparador", "📐 Patrones"])

# ================= PESTAÑA 1: SEÑAL =================
with tab_sig:
    c1, c2 = st.columns(2)
    ticker = c1.selectbox("Instrumento", TICKERS, index=0)
    timeframe = c2.selectbox("Marco temporal", list(core.TIMEFRAMES), index=0)
    capital = st.number_input("Mi capital ($)", min_value=100.0,
                              value=5000.0, step=100.0)
    try:
        df = candles_with_ind(ticker, timeframe)
    except Exception as e:
        st.error(f"No pude descargar {ticker}: {e}")
        st.stop()
    last = df.iloc[-1]
    sig = core.signal(last)
    unidad = core.TIMEFRAMES[timeframe][1]
    pv = live_price(ticker)
    clase = {"LARGO": "verde", "CORTO": "rojo", "ESPERAR": "amarillo"}[sig]
    texto = {"LARGO": "🟢 IR LARGO", "CORTO": "🔴 IR CORTO",
             "ESPERAR": "🟡 ESPERAR"}[sig]
    st.markdown(f'<div class="banner {clase}">{texto}</div>',
                unsafe_allow_html=True)
    st.markdown(f'<div class="sub">{ticker} · vela {timeframe.lower()} '
                f'cerrada el {df.index[-1]:%d/%m/%Y} · precio '
                f'${pv:,.2f}</div>', unsafe_allow_html=True)
    st.write("")
    if sig in ("LARGO", "CORTO"):
        sl, tp = core.levels(float(last.Close), float(last.ATR), sig)
        unids = core.position_size(capital, float(last.ATR))
        m1, m2 = st.columns(2)
        m1.metric("Stop Loss", f"${sl:,.2f}")
        m2.metric("Take Profit", f"${tp:,.2f}")
        m3, m4 = st.columns(2)
        m3.metric("Tamaño (riesgo 1%)",
                  f"{unids:,.1f} unid. (~${unids*last.Close:,.0f})")
        m4.metric("Tiempo estimado",
                  core.holding_estimate(float(last.Close), tp,
                                        float(last.ATR), unidad))
        st.caption("Ejecuta en la próxima apertura. Gap >±3-4%: descarta.")
    else:
        st.info("Sin señal: condiciones no alineadas.")
    with st.expander("📊 Detalle técnico"):
        d1, d2, d3 = st.columns(3)
        d1.metric("RSI (14)", f"{last.RSI:,.1f}")
        d2.metric("MACD − Señal", f"{last.MACD-last.Senal:,.2f}")
        d3.metric("ATR (14)", f"${last.ATR:,.2f}")
        st.line_chart(df[["Close", "SMA10", "SMA30"]].tail(80))
    with st.expander("🧪 Backtest de este instrumento"):
        bt = core.backtest(df.dropna(subset=["ATR"]), capital=capital)
        m = bt.metrics(capital)
        b1, b2, b3 = st.columns(3)
        b1.metric("Operaciones", m["operaciones"])
        b2.metric("% acierto", f"{m['acierto']:.0%}")
        b3.metric("Neto", f"${m['neto']:,.0f} ({m['neto_pct']:+.1%})")
        if bt.equity is not None and len(bt.equity):
            st.line_chart(bt.equity)

# ================= PESTAÑA 2: MIS POSICIONES =================
with tab_pos:
    st.subheader("💼 Asesor de salida en vivo")
    st.caption("Registra tus operaciones reales. La app las vigila con las "
               "reglas de la estrategia + trailing stop de 1.5×ATR y te dice "
               "cuándo cerrar. Nadie conoce el máximo exacto por adelantado: "
               "el trailing captura la mayor parte de la tendencia sin "
               "devolverlo todo en un giro.")
    with st.form("nueva_pos", clear_on_submit=True):
        f1, f2, f3 = st.columns(3)
        p_tk = f1.selectbox("Ticker", TICKERS)
        p_dir = f2.selectbox("Dirección", ["LARGO", "CORTO"])
        p_tf = f3.selectbox("Marco", list(core.TIMEFRAMES))
        f4, f5, f6 = st.columns(3)
        p_fecha = f4.date_input("Fecha de entrada")
        p_precio = f5.number_input("Precio de entrada", min_value=0.01,
                                   value=100.0, format="%.2f")
        p_unids = f6.number_input("Unidades", min_value=0.01, value=1.0,
                                  format="%.2f")
        auto = st.checkbox("Calcular SL/TP automáticos (ATR a la fecha de "
                           "entrada)", value=True)
        f7, f8 = st.columns(2)
        p_sl = f7.number_input("SL manual", min_value=0.0, value=0.0,
                               format="%.2f", disabled=auto)
        p_tp = f8.number_input("TP manual", min_value=0.0, value=0.0,
                               format="%.2f", disabled=auto)
        if st.form_submit_button("➕ Agregar posición"):
            dfp = candles_with_ind(p_tk, p_tf)
            if auto or not (p_sl and p_tp):
                sl, tp, _ = core.initial_levels_at(dfp, p_fecha,
                                                   p_precio, p_dir)
            else:
                sl, tp = p_sl, p_tp
            poss = load_positions()
            poss.append({"ticker": p_tk, "direccion": p_dir, "marco": p_tf,
                         "fecha": str(p_fecha), "entrada": p_precio,
                         "unidades": p_unids, "sl": round(sl, 2),
                         "tp": round(tp, 2)})
            save_positions(poss)
            st.success("Posición agregada.")

    positions = load_positions()
    if not positions:
        st.info("Sin posiciones registradas. Agrega tu primera compra arriba.")
    total_pnl = 0.0
    for i, p in enumerate(positions):
        try:
            dfp = candles_with_ind(p["ticker"], p.get("marco", "Semanal"))
            pv = live_price(p["ticker"])
            ev = core.evaluate_position(p, dfp, pv)
        except Exception as e:
            st.warning(f"{p['ticker']}: no pude evaluar ({e})")
            continue
        total_pnl += ev["pnl"]
        clase = {"🔴": "rojo", "🟠": "naranja", "🟡": "amarillo",
                 "🟢": "verde"}[ev["recomendacion"][:1]]
        with st.container(border=True):
            st.markdown(
                f'<div class="banner {clase}" style="font-size:22px;'
                f'padding:12px;">{p["ticker"]} {p["direccion"]} — '
                f'{ev["recomendacion"]}</div>', unsafe_allow_html=True)
            st.write(ev["motivo"])
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("P&L", f"${ev['pnl']:,.0f}", f"{ev['pnl_pct']:+.1%}")
            k2.metric("Precio", f"${pv:,.2f}",
                      f"entrada ${p['entrada']:,.2f}")
            k3.metric("SL / TP", f"${ev['sl']:,.2f} / ${ev['tp']:,.2f}")
            k4.metric("Stop sugerido hoy", f"${ev['stop_sugerido']:,.2f}")
            st.caption(f"{p['unidades']} unid. desde {p['fecha']} · marco "
                       f"{p.get('marco','Semanal')} · vela evaluada: "
                       f"{ev['vela_cerrada']:%d/%m/%Y}")
            if st.button("🗑️ Cerrar/eliminar", key=f"del{i}"):
                positions.pop(i)
                save_positions(positions)
                st.rerun()
    if positions:
        st.metric("P&L total de la cartera", f"${total_pnl:,.0f}")
    b1, b2 = st.columns(2)
    b1.download_button("⬇️ Respaldar posiciones (JSON)",
                       json.dumps(positions, indent=2, default=str),
                       "positions.json")
    up = b2.file_uploader("⬆️ Restaurar respaldo", type="json",
                          label_visibility="collapsed")
    if up is not None:
        save_positions(json.loads(up.read()))
        st.success("Respaldo restaurado.")
        st.rerun()
    st.caption("⚠️ En Streamlit Cloud gratuito el archivo de posiciones "
               "puede borrarse cuando la app se reinicia: descarga el "
               "respaldo JSON tras cada cambio y restáuralo si hace falta.")

# ================= PESTAÑA 3: COMPARADOR =================
with tab_cmp:
    st.subheader("🏆 ¿Qué instrumento le sienta mejor a la estrategia?")
    sel = st.multiselect("Instrumentos a comparar", TICKERS,
                         default=["BABA", "QQQ", "SPY", "MSFT"])
    tf_cmp = st.selectbox("Marco", list(core.TIMEFRAMES), index=0,
                          key="tfcmp")
    if sel:
        datasets = {}
        for tk in sel:
            try:
                datasets[tk] = fetch_daily(tk)
            except Exception:
                st.warning(f"{tk}: sin datos, lo omito.")
        tabla = core.compare_instruments(datasets, tf_cmp, capital=5000)
        if len(tabla):
            mejor = tabla.iloc[0]
            st.success(f"🏅 Mejor desempeño reciente: **{mejor['Ticker']}** "
                       f"({mejor['Neto % 12m']:+.1%} en 12 meses, "
                       f"{mejor['Acierto']:.0%} de acierto histórico)")
            st.dataframe(tabla.style.format({
                "Acierto": "{:.0%}", "Neto %": "{:+.1%}",
                "Neto % 12m": "{:+.1%}", "Profit factor": "{:.2f}",
                "Máx DD %": "{:.1%}"}), use_container_width=True,
                hide_index=True)
            st.bar_chart(tabla.set_index("Ticker")[["Neto %",
                                                    "Neto % 12m"]])
            st.caption("Se recalcula solo con cada vela nueva (los datos se "
                       "refrescan al abrir la app). 'Neto % 12m' pondera la "
                       "eficacia reciente; el histórico completo, la "
                       "consistencia. Ojo: el mejor del pasado no está "
                       "garantizado a futuro — usa esto para descartar "
                       "instrumentos donde la estrategia claramente no "
                       "funciona, y revisa la comparación cada 1-2 meses.")

# ================= PESTAÑA 4: PATRONES =================
with tab_pat:
    st.subheader("📐 Patrones chartistas (largo plazo)")
    c1, c2 = st.columns(2)
    tk_pat = c1.selectbox("Instrumento", TICKERS, key="tkpat")
    tf_pat = c2.selectbox("Marco", list(core.TIMEFRAMES), index=0,
                          key="tfpat")
    dfp = candles_with_ind(tk_pat, tf_pat)
    pats = core.detect_patterns(dfp, lookback=min(120, len(dfp)))
    tail = dfp.tail(120)
    fig = go.Figure(data=[go.Candlestick(
        x=tail.index, open=tail.Open, high=tail.High,
        low=tail.Low, close=tail.Close, name=tk_pat)])
    fig.add_scatter(x=tail.index, y=tail.SMA10, name="SMA10",
                    line=dict(width=1.2))
    fig.add_scatter(x=tail.index, y=tail.SMA30, name="SMA30",
                    line=dict(width=1.2))
    for p in pats:
        fig.add_vrect(x0=p["desde"], x1=p["hasta"],
                      fillcolor="orange", opacity=0.12, line_width=0)
        if p["nivel"]:
            fig.add_hline(y=p["nivel"], line_dash="dot", line_width=1,
                          annotation_text=f'{p["patron"]}')
    fig.update_layout(height=420, xaxis_rangeslider_visible=False,
                      margin=dict(l=10, r=10, t=25, b=10),
                      legend=dict(orientation="h"))
    st.plotly_chart(fig, use_container_width=True)
    if not pats:
        st.info("No detecto patrones clásicos en las últimas ~120 velas. "
                "La ausencia de patrón también es información: mercado sin "
                "estructura clara.")
    for p in pats:
        emoji = {"alcista": "🟢", "bajista": "🔴"}.get(
            p["sesgo"].split()[0], "🟡")
        with st.container(border=True):
            st.markdown(f"**{emoji} {p['patron']}** — sesgo {p['sesgo']} · "
                        f"*{p['estado']}*")
            st.write(p["detalle"])
            st.caption(f"Ventana: {p['desde']:%d/%m/%Y} → "
                       f"{p['hasta']:%d/%m/%Y}")
    st.caption("Detección heurística sobre pivotes: los patrones chartistas "
               "son subjetivos y esta identificación automática es una "
               "aproximación educativa, no una señal operativa. Un patrón "
               "'en formación' puede deshacerse; espera siempre la "
               "confirmación (ruptura con vela cerrada) y contrástalo con "
               "el semáforo antes de decidir.")

st.caption("⚠️ Herramienta educativa, no asesoría financiera. Datos Yahoo "
           "Finance (precio vivo ±15 min).")
