# -*- coding: utf-8 -*-
"""app.py — Semáforo de swing trading (réplica en Python de la plantilla
de Google Sheets). Datos: Yahoo Finance vía yfinance, actualizados poco
después del cierre de NY, sin la fragilidad de GOOGLEFINANCE.

Ejecutar local:   streamlit run app.py
Desplegar gratis: Streamlit Community Cloud (ver README.md).
"""
import pandas as pd
import streamlit as st
import yfinance as yf

import core

st.set_page_config(page_title="Semáforo Swing", page_icon="🚦",
                   layout="centered")

# ---------- estilos mobile-first: el semáforo es el héroe ----------
st.markdown("""
<style>
.banner {border-radius:14px; padding:26px 10px; text-align:center;
         font-size:32px; font-weight:800; letter-spacing:1px;}
.verde    {background:#34A853; color:white;}
.rojo     {background:#EA4335; color:white;}
.amarillo {background:#FBBC04; color:#222;}
.sub {text-align:center; color:#888; font-size:13px; margin-top:4px;}
div[data-testid="stMetricValue"] {font-size:22px;}
</style>
""", unsafe_allow_html=True)

st.title("🚦 Semáforo Swing")

TICKERS = ["BABA", "QQQ", "SPY", "MSFT", "AAPL", "GLD"]
c1, c2 = st.columns(2)
ticker = c1.selectbox("Instrumento", TICKERS, index=0)
timeframe = c2.selectbox("Marco temporal", list(core.TIMEFRAMES), index=0)
capital = st.number_input("Mi capital ($)", min_value=100.0,
                          value=5000.0, step=100.0)


@st.cache_data(ttl=900, show_spinner="Descargando datos de Yahoo Finance…")
def fetch_daily(symbol: str) -> pd.DataFrame:
    df = yf.download(symbol, period="6y", interval="1d",
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):      # yfinance>=0.2 agrupa
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


try:
    daily = fetch_daily(ticker)
except Exception as e:                              # red caída, ticker malo…
    st.error(f"No pude descargar datos de {ticker}. Reintenta en unos "
             f"minutos o revisa el ticker. Detalle: {e}")
    st.stop()

if daily.empty:
    st.error(f"Yahoo no devolvió datos para {ticker}.")
    st.stop()

candles = core.resample_ohlc(daily, timeframe)
candles = core.drop_open_candle(candles, pd.Timestamp.now(tz="UTC"))
df = core.add_indicators(candles)

last = df.iloc[-1]
sig = core.signal(last)
unidad = core.TIMEFRAMES[timeframe][1]
precio_vivo = float(daily["Close"].iloc[-1])

# ---------- semáforo ----------
clase = {"LARGO": "verde", "CORTO": "rojo", "ESPERAR": "amarillo"}[sig]
texto = {"LARGO": "🟢 IR LARGO", "CORTO": "🔴 IR CORTO",
         "ESPERAR": "🟡 ESPERAR"}[sig]
st.markdown(f'<div class="banner {clase}">{texto}</div>',
            unsafe_allow_html=True)
st.markdown(f'<div class="sub">{ticker} · vela {timeframe.lower()} cerrada '
            f'el {df.index[-1]:%d/%m/%Y} · último precio '
            f'${precio_vivo:,.2f}</div>', unsafe_allow_html=True)
st.write("")

# ---------- niveles de la operación ----------
if sig in ("LARGO", "CORTO"):
    sl, tp = core.levels(float(last.Close), float(last.ATR), sig)
    unidades = core.position_size(capital, float(last.ATR))
    m1, m2 = st.columns(2)
    m1.metric("Stop Loss", f"${sl:,.2f}")
    m2.metric("Take Profit", f"${tp:,.2f}")
    m3, m4 = st.columns(2)
    m3.metric("Tamaño (riesgo 1%)",
              f"{unidades:,.1f} unid. (~${unidades * last.Close:,.0f})")
    m4.metric("Tiempo estimado",
              core.holding_estimate(float(last.Close), tp,
                                    float(last.ATR), unidad))
    st.caption("Ejecuta en la próxima apertura. Si abre con gap >±3-4% "
               "respecto al cierre, descarta la señal.")
else:
    st.info("Sin señal: condiciones no alineadas. La paciencia también "
            "es una posición.")

# ---------- detalle técnico ----------
with st.expander("📊 Detalle técnico"):
    d1, d2, d3 = st.columns(3)
    d1.metric("RSI (14)", f"{last.RSI:,.1f}")
    d2.metric("MACD − Señal", f"{last.MACD - last.Senal:,.2f}")
    d3.metric("ATR (14)", f"${last.ATR:,.2f}")
    d4, d5, d6 = st.columns(3)
    d4.metric("Cierre", f"${last.Close:,.2f}")
    d5.metric("SMA 10", f"${last.SMA10:,.2f}")
    d6.metric("SMA 30", f"${last.SMA30:,.2f}")
    st.line_chart(df[["Close", "SMA10", "SMA30"]].tail(80),
                  color=["#1f77b4", "#ff7f0e", "#2ca02c"])

# ---------- backtest ----------
with st.expander("🧪 Backtest de la estrategia (histórico completo)"):
    bt = core.backtest(df.dropna(subset=["ATR"]), capital=capital)
    m = bt.metrics(capital)
    b1, b2, b3 = st.columns(3)
    b1.metric("Operaciones", m["operaciones"])
    b2.metric("% acierto", f"{m['acierto']:.0%}")
    b3.metric("Neto", f"${m['neto']:,.0f} ({m['neto_pct']:+.1%})")
    b4, b5, b6 = st.columns(3)
    b4.metric("Ganancia media", f"${m['gan_media']:,.0f}")
    b5.metric("Pérdida media", f"${m['perd_media']:,.0f}")
    b6.metric("Máx. drawdown", f"${m['max_drawdown']:,.0f}")
    if bt.equity is not None and len(bt.equity):
        st.line_chart(bt.equity)
    cerrados = [t for t in bt.trades if t.fecha_salida is not None]
    if cerrados:
        st.dataframe(pd.DataFrame([{
            "Entrada": t.fecha_entrada.date(), "Dir": t.direccion,
            "Precio": round(t.entrada, 2), "Salida": t.fecha_salida.date(),
            "Motivo": t.motivo, "Resultado $": round(t.resultado, 2),
        } for t in cerrados]), use_container_width=True, hide_index=True)
    st.caption("Sin spread, comisiones nocturnas ni gaps que salten el "
               "stop: la realidad será algo peor. Muestra pequeña — sirve "
               "para descartar estrategias malas, no para prometer "
               "resultados.")

st.caption("⚠️ Herramienta educativa, no asesoría financiera. Datos de "
           "Yahoo Finance con posible retraso de ~15 min en el precio "
           "vivo; los históricos se consolidan tras el cierre de NY.")
