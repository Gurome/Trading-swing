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


STRAT_FILE = Path(__file__).parent / "strategy.json"


def load_strategy():
    """Estrategia elegida en el Laboratorio; None = la original."""
    if STRAT_FILE.exists():
        try:
            return core.StrategyConfig(**json.loads(STRAT_FILE.read_text()))
        except Exception:
            return None
    return None


def save_strategy(cfg):
    from dataclasses import asdict
    STRAT_FILE.write_text(json.dumps(asdict(cfg)))


def clear_strategy():
    if STRAT_FILE.exists():
        STRAT_FILE.unlink()


st.title("🚦 Semáforo Swing")
tab_sig, tab_pos, tab_cmp, tab_pat, tab_lab = st.tabs(
    ["🚦 Señal", "💼 Posiciones", "🏆 Comparador", "📐 Patrones",
     "🔬 Laboratorio"])

# ================= PESTAÑA 1: SEÑAL =================
with tab_sig:
    cfg_act = load_strategy()
    es_custom = cfg_act is not None
    cfg = cfg_act if es_custom else core.StrategyConfig()
    if es_custom:
        ic1, ic2 = st.columns([4, 1])
        ic1.info(f"🔬 Estrategia del Laboratorio activa: "
                 f"**{cfg.etiqueta()}** · riesgo {cfg.riesgo:.1%}")
        if ic2.button("↩️ Volver a la original"):
            clear_strategy()
            st.rerun()
    c1, c2 = st.columns(2)
    ticker = c1.selectbox("Instrumento", TICKERS, index=0)
    timeframe = c2.selectbox("Marco temporal", list(core.TIMEFRAMES), index=0)
    capital = st.number_input("Mi capital ($)", min_value=100.0,
                              value=5000.0, step=100.0)
    try:
        candles = core.resample_ohlc(fetch_daily(ticker), timeframe)
        candles = core.drop_open_candle(candles, pd.Timestamp.now(tz="UTC"))
        df = core.prepare(candles, cfg)
    except Exception as e:
        st.error(f"No pude descargar {ticker}: {e}")
        st.stop()
    last = df.iloc[-1]
    sig = core.signal_cfg(last, cfg)
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
        atr = float(last.ATR)
        if sig == "LARGO":
            sl, tp = last.Close - cfg.sl_atr * atr, last.Close + cfg.tp_atr * atr
        else:
            sl, tp = last.Close + cfg.sl_atr * atr, last.Close - cfg.tp_atr * atr
        unids = capital * cfg.riesgo / (cfg.sl_atr * atr)
        m1, m2 = st.columns(2)
        m1.metric("Stop Loss", f"${sl:,.2f}")
        m2.metric("Take Profit", f"${tp:,.2f}")
        m3, m4 = st.columns(2)
        m3.metric(f"Tamaño (riesgo {cfg.riesgo:.1%})",
                  f"{unids:,.1f} unid. (~${unids*last.Close:,.0f})")
        m4.metric("Tiempo estimado",
                  core.holding_estimate(float(last.Close), tp, atr, unidad))
        st.caption("Ejecuta en la próxima apertura. Gap >±3-4%: descarta.")
    else:
        st.info("Sin señal: condiciones no alineadas.")
    with st.expander("📊 Detalle técnico"):
        d1, d2, d3 = st.columns(3)
        d1.metric("RSI (14)", f"{last.RSI:,.1f}")
        d2.metric("MACD − Señal", f"{last.MACD-last.Senal:,.2f}")
        d3.metric("ATR (14)", f"${last.ATR:,.2f}")
        chart = df[["Close", "SMAf", "SMAs"]].tail(80).rename(columns={
            "SMAf": f"SMA {cfg.sma_fast}", "SMAs": f"SMA {cfg.sma_slow}"})
        st.line_chart(chart)
    with st.expander("🧪 Backtest de este instrumento con esta estrategia"):
        bt = core.backtest_cfg(df.dropna(subset=["ATR"]), cfg,
                               capital=capital)
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
    st.subheader("📐 Patrones chartistas")
    c1, c2 = st.columns(2)
    tk_pat = c1.selectbox("Instrumento", TICKERS, key="tkpat")
    tf_pat = c2.selectbox("Marco", list(core.TIMEFRAMES), index=0,
                          key="tfpat")
    dfp = candles_with_ind(tk_pat, tf_pat)
    # Detecta sobre suficiente historia para VER la estructura, pero solo
    # conserva patrones VIGENTES: los que terminan dentro del último mes.
    pats_all = core.detect_patterns_v2(dfp, lookback=min(120, len(dfp)))
    corte_rec = dfp.index[-1] - pd.Timedelta(days=30)
    pats = [p for p in pats_all if p["hasta"] >= corte_rec]
    # gráfico centrado en lo reciente: última data + el contexto justo
    # para ver el patrón vigente completo
    if pats:
        inicio = min(p["desde"] for p in pats)
        tail = dfp[dfp.index >= inicio]
        if len(tail) < 20:
            tail = dfp.tail(20)
    else:
        tail = dfp.tail(20)
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
                          annotation_text=p["patron"])
    fig.update_layout(height=420, xaxis_rangeslider_visible=False,
                      margin=dict(l=10, r=10, t=25, b=10),
                      legend=dict(orientation="h"))
    st.plotly_chart(fig, use_container_width=True)

    # ---- 1) Patrón por confirmarse / recién confirmado (lo más reciente) ----
    st.markdown("#### 🎯 Ahora mismo")
    st.caption("Solo se muestran patrones vigentes (que terminan en los "
               "últimos 30 días). Los antiguos no aparecen aquí: viven en "
               "el análisis de fiabilidad histórica de abajo.")
    if not pats:
        st.info("Sin patrones vigentes en el último mes. La ausencia de "
                "estructura también es información: mercado sin figura "
                "clara ahora mismo.")
    for p in pats:
        emoji = {"alcista": "🟢", "bajista": "🔴"}.get(
            str(p["sesgo"]).split()[0], "🟡")
        with st.container(border=True):
            titulo = f"**{emoji} {p['patron']}** — {p['sesgo']}"
            if p.get("recien_confirmado"):
                st.markdown(f'<div class="banner {"verde" if p["alcista"] else "rojo"}" '
                            f'style="font-size:20px;padding:10px;">⚡ '
                            f'{p["patron"]} CONFIRMADO en la última vela'
                            f'</div>', unsafe_allow_html=True)
            else:
                st.markdown(f"{titulo} · *{p['estado']}*")
            st.write(p["detalle"])
            if p.get("nivel_ruptura") is not None:
                entrada = p["nivel_ruptura"]
                stop = p["invalidacion"]
                obj = p["objetivo"]
                rr = abs(obj - entrada) / abs(entrada - stop)
                g1, g2, g3, g4 = st.columns(4)
                g1.metric("Confirma en", f"${entrada:,.2f}")
                g2.metric("Stop (invalidación)", f"${stop:,.2f}")
                g3.metric("Objetivo", f"${obj:,.2f}")
                g4.metric("R (beneficio/riesgo)", f"{rr:,.1f}")
                st.caption("Plan SOLO si confirma con vela cerrada; sin "
                           "confirmación no hay operación. Estrategia B "
                           "(agresiva): riesgo sugerido 0.5% del capital, "
                           "la mitad que el semáforo.")
            st.caption(f"Ventana: {p['desde']:%d/%m/%Y} → "
                       f"{p['hasta']:%d/%m/%Y}")

    # ---- 2) Fiabilidad histórica con validación 70/30 ----
    st.markdown("#### 🧪 ¿Qué tan bien cumplen los patrones aquí?")
    if st.button("Analizar fiabilidad histórica", key="btn_rel"):
        with st.spinner("Escaneando todo el histórico vela a vela…"):
            rel = core.pattern_reliability(dfp)
        m = rel["metricas"]
        if not m["eventos"]:
            st.info("El escáner no encontró confirmaciones históricas en "
                    "este instrumento/marco.")
        else:
            r1, r2, r3 = st.columns(3)
            r1.metric("Confirmaciones", m["eventos"])
            r2.metric("Cumplieron objetivo", f"{m['cumplimiento']:.0%}")
            r3.metric("R acumulado", f"{m['r_total']:+.1f}R "
                      f"({m['neto_pct']:+.1%} al 0.5%)")
            st.dataframe(rel["resumen"].style.format({
                "Cumplimiento": "{:.0%}", "R medio": "{:+.2f}",
                "Cumpl. IS": "{:.0%}", "Cumpl. OOS": "{:.0%}"},
                na_rep="—"), use_container_width=True, hide_index=True)
            if len(rel["equity"]):
                st.line_chart(rel["equity"])
            st.caption("**Cómo leerlo:** 'Cumpl. IS' es el primer 70% del "
                       "histórico; 'Cumpl. OOS' el 30% final — el examen "
                       "con datos nuevos. Un patrón fiable mantiene "
                       "cumplimiento similar en ambos y R medio positivo. "
                       "Pocas confirmaciones (<10) = evidencia débil, no "
                       "concluyas nada. El R acumulado ya descuenta los "
                       "fallos: es el resultado de operar TODOS los "
                       "patrones mecánicamente con stop en la invalidación.")
            st.caption("**Doble estrategia sugerida:** A = semáforo "
                       "(conservadora, 1% riesgo) como base; B = patrones "
                       "(agresiva, 0.5% riesgo) solo en los tipos con "
                       "Cumpl. OOS ≥ 50% y R medio positivo en este "
                       "instrumento. Si A y B se contradicen en el mismo "
                       "instrumento (una larga y otra corta), no operes "
                       "ninguna: el mercado no está claro.")
    st.caption("Detección heurística sobre pivotes: aproximación educativa, "
               "no señal infalible. Los pivotes necesitan velas para "
               "madurar, así que un patrón puede aparecer 1-2 velas "
               "después de formarse.")

st.caption("⚠️ Herramienta educativa, no asesoría financiera. Datos Yahoo "
           "Finance (precio vivo ±15 min).")

# ================= PESTAÑA 5: LABORATORIO =================
with tab_lab:
    st.subheader("🔬 Laboratorio de estrategias")
    with st.expander("⚠️ Lee esto antes de optimizar (importante)",
                     expanded=True):
        st.markdown(
            "Si pruebas suficientes combinaciones, **siempre** aparecerá "
            "alguna con un backtest espectacular — igual que si torturas "
            "los datos, confiesan lo que quieras. Eso se llama "
            "**sobreajuste** y es la forma #1 en que los traders se "
            "autoengañan.\n\n"
            "Por eso este laboratorio divide el histórico en dos: optimiza "
            "con el primer 70% (**IS**, in-sample) y examina con el 30% "
            "final (**OOS**, out-of-sample) — datos que la búsqueda nunca "
            "vio, como un examen con preguntas nuevas. **La columna que "
            "importa es 'Anual OOS'**; una estrategia que brilla en IS y "
            "fracasa en OOS lleva la bandera ⚠️ de sobreajuste.\n\n"
            "Honestidad: un 30% anual *sostenido* es rendimiento de élite "
            "mundial. Si aparece aquí un 30% OOS, sospecha primero "
            "(muestra corta, suerte, un solo mercado) y verifica en otros "
            "instrumentos y en papel antes de creerlo.")
    lc1, lc2, lc3 = st.columns(3)
    tk_lab = lc1.selectbox("Instrumento", TICKERS, key="tklab")
    tf_lab = lc2.selectbox("Marco", list(core.TIMEFRAMES), key="tflab")
    riesgo_lab = lc3.select_slider("Riesgo por operación",
                                   options=[0.005, 0.01, 0.015, 0.02],
                                   value=0.01,
                                   format_func=lambda x: f"{x:.1%}")
    st.caption("El riesgo escala el resultado casi linealmente: 2% de "
               "riesgo ≈ el doble de retorno **y el doble de drawdown** "
               "que 1%. No crea ventaja, solo la amplifica — en ambas "
               "direcciones.")
    candles_lab = core.resample_ohlc(fetch_daily(tk_lab), tf_lab)
    candles_lab = core.drop_open_candle(candles_lab,
                                        pd.Timestamp.now(tz="UTC"))
    modo = st.radio("Modo", ["Probar una estrategia (manual)",
                             "Auto-explorar (búsqueda con validación)"],
                    horizontal=True)

    if modo.startswith("Probar"):
        tipo = st.selectbox("Tipo de estrategia", [
            ("tendencia", "Tendencia confirmada (la original)"),
            ("cruce", "Cruce de medias"),
            ("donchian", "Ruptura Donchian (canal de N velas)"),
            ("pullback", "Pullback (comprar el retroceso en tendencia)")],
            format_func=lambda t: t[1])[0]
        pc1, pc2, pc3 = st.columns(3)
        sma_f = pc1.number_input("Media rápida", 3, 30, 10)
        sma_s = pc2.number_input("Media lenta", 15, 100, 30)
        don = pc3.number_input("Canal Donchian (velas)", 3, 30, 8)
        pc4, pc5 = st.columns(2)
        rsi_band = pc4.slider("Banda RSI (largos)", 20, 90, (50, 70))
        usar_macd = pc5.checkbox("Exigir confirmación MACD", value=True)
        pc6, pc7 = st.columns(2)
        sl_m = pc6.slider("Stop loss (×ATR)", 0.5, 3.0, 1.5, 0.25)
        tp_m = pc7.slider("Take profit (×ATR)", 1.0, 6.0, 3.0, 0.5)
        cfg = core.StrategyConfig(
            tipo=tipo, sma_fast=int(sma_f), sma_slow=int(sma_s),
            rsi_low=float(rsi_band[0]), rsi_high=float(rsi_band[1]),
            usar_macd=usar_macd, donchian=int(don),
            sl_atr=float(sl_m), tp_atr=float(tp_m), riesgo=riesgo_lab)
        r = core.evaluate_config(candles_lab, cfg, capital=5000)
        e1, e2, e3 = st.columns(3)
        e1.metric("Anual IS (70% inicial)", f"{r['Anual IS']:+.1%}")
        e2.metric("Anual OOS (30% final)", f"{r['Anual OOS']:+.1%}")
        e3.metric("Sobreajuste", r["Sobreajuste"])
        e4, e5, e6 = st.columns(3)
        e4.metric("Operaciones", r["Ops"])
        e5.metric("% acierto", f"{r['Acierto']:.0%}")
        e6.metric("Máx drawdown", f"{r['Máx DD %']:.1%}")
        dfl = core.prepare(candles_lab, cfg).dropna(subset=["ATR"])
        btl = core.backtest_cfg(dfl, cfg, 5000)
        if btl.equity is not None and len(btl.equity):
            st.line_chart(btl.equity)
        st.caption(f"Config: {cfg.etiqueta()} · riesgo {riesgo_lab:.1%}")
        if st.button("✅ Usar esta estrategia en 🚦 Señal", type="primary"):
            save_strategy(cfg)
            st.success("Estrategia activada. La pestaña 🚦 Señal ya opera "
                       "con estas reglas (verás el aviso azul arriba; desde "
                       "ahí puedes volver a la original).")

    else:
        tipos_sel = st.multiselect(
            "Tipos a explorar",
            ["tendencia", "cruce", "donchian", "pullback"],
            default=["tendencia", "cruce", "donchian", "pullback"])
        n_iter = st.slider("Combinaciones a probar", 30, 300, 100, 10)
        if st.button("🔬 Buscar", type="primary") and tipos_sel:
            cfgs = core.random_configs(tipos_sel, n_iter,
                                       riesgo=riesgo_lab)
            barra = st.progress(0.0, "Probando configuraciones…")
            filas = []
            for i, c in enumerate(cfgs):
                try:
                    filas.append(core.evaluate_config(candles_lab, c,
                                                      capital=5000))
                except Exception:
                    pass
                barra.progress((i + 1) / len(cfgs))
            barra.empty()
            filas = [f for f in filas if f["Ops"] >= 8]
            filas.sort(key=lambda f: -f["Anual OOS"])
            st.session_state["lab_res"] = filas[:15]
            st.session_state["lab_ctx"] = f"{tk_lab} · {tf_lab}"
        if st.session_state.get("lab_res"):
            filas = st.session_state["lab_res"]
            st.caption(f"Resultados para {st.session_state['lab_ctx']} "
                       f"(top {len(filas)}, ordenados por Anual OOS)")
            tabla = pd.DataFrame(filas).drop(columns="cfg")
            st.dataframe(tabla.style.format({
                "Anual IS": "{:+.1%}", "Anual OOS": "{:+.1%}",
                "Acierto": "{:.0%}", "Máx DD %": "{:.1%}"}),
                use_container_width=True, hide_index=True)
            mejor = filas[0]
            st.success(f"Mejor validada: **{mejor['Estrategia']}** → "
                       f"{mejor['Anual OOS']:+.1%} anual en datos que "
                       f"nunca vio (IS {mejor['Anual IS']:+.1%}).")
            opciones = {f"{f['Estrategia']} · OOS {f['Anual OOS']:+.1%}"
                        + (" ⚠️" if f["Sobreajuste"] == "⚠️ sí" else ""): f
                        for f in filas}
            elegida = st.selectbox("Elegir estrategia para operar",
                                   list(opciones))
            if st.button("✅ Usar la elegida en 🚦 Señal", type="primary"):
                cfg_sel = opciones[elegida]["cfg"]
                cfg_sel.riesgo = riesgo_lab
                save_strategy(cfg_sel)
                st.success("Estrategia activada en 🚦 Señal (aviso azul "
                           "arriba de esa pestaña; desde ahí vuelves a la "
                           "original cuando quieras).")
                if opciones[elegida]["Sobreajuste"] == "⚠️ sí":
                    st.warning("Elegiste una config con bandera de "
                               "sobreajuste: su ventaja no sobrevivió al "
                               "examen OOS. Úsala solo en papel.")
            st.caption("Siguiente paso serio: prueba la elegida en modo "
                       "Manual sobre OTROS instrumentos y marcos. Si la "
                       "ventaja solo existe en un ticker, era ruido. Y aun "
                       "la mejor: 2-3 meses en papel antes de dinero real. "
                       "Compárala siempre contra tu ~5% bancario sin "
                       "riesgo.")
