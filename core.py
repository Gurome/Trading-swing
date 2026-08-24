# -*- coding: utf-8 -*-
"""core.py — Lógica de la estrategia "Tendencia Confirmada".

Réplica exacta de las reglas de la plantilla de Google Sheets:
  - Señal:  LARGO si Close>SMA30 y Close>SMA10 y 50<RSI<70 y MACD>Señal
            CORTO (espejo) · ESPERAR en cualquier otro caso.
  - SL = entrada ∓ 1.5×ATR · TP = entrada ± 3×ATR · riesgo 1% del capital.
  - Backtest: entrada en la apertura de la vela siguiente a la señal;
    salida por SL (prioridad, criterio conservador), TP o cierre cruzando
    la SMA10. Sin spread ni comisiones (la realidad será algo peor).
Sin dependencias de Streamlit: importable y testeable por separado.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

RSI_LONG = (50, 70)
RSI_SHORT = (30, 50)
SL_ATR = 1.5
TP_ATR = 3.0
RISK_PCT = 0.01

TIMEFRAMES = {
    "Semanal": ("W-FRI", "semanas"),
    "Quincenal": ("2W-FRI", "quincenas"),
    "Diario": (None, "días hábiles"),
}


def resample_ohlc(daily: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Agrega velas diarias al marco pedido ('Semanal'/'Quincenal'/'Diario')."""
    rule, _ = TIMEFRAMES[timeframe]
    if rule is None:
        return daily.copy()
    out = daily.resample(rule).agg(
        {"Open": "first", "High": "max", "Low": "min",
         "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Close"])
    return out


def drop_open_candle(candles: pd.DataFrame, now_utc: pd.Timestamp) -> pd.DataFrame:
    """Elimina la última vela si aún no cierra (NY cierra 21:00 UTC aprox;
    margen a 21:10). La etiqueta de cada vela es su fecha de cierre."""
    if candles.empty:
        return candles
    label = candles.index[-1]
    cierre = label.tz_localize("UTC") if label.tzinfo is None else label
    cierre = cierre.normalize() + pd.Timedelta(hours=21, minutes=10)
    if now_utc < cierre:
        return candles.iloc[:-1]
    return candles


def add_indicators(candles: pd.DataFrame) -> pd.DataFrame:
    """SMA 10/30, MACD 12-26-9, RSI 14 (Wilder) y ATR 14 (Wilder)."""
    df = candles.copy()
    c = df["Close"]
    df["SMA10"] = c.rolling(10).mean()
    df["SMA30"] = c.rolling(30).mean()
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["Senal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    delta = c.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    df["RSI"] = 100 - 100 / (1 + avg_gain / avg_loss)
    df.loc[avg_loss == 0, "RSI"] = 100.0
    prev = c.shift(1)
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev).abs(),
         (df["Low"] - prev).abs()], axis=1
    ).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    return df


def signal(row) -> str:
    """Devuelve 'LARGO', 'CORTO' o 'ESPERAR' para una vela con indicadores."""
    needed = [row.SMA10, row.SMA30, row.RSI, row.MACD, row.Senal, row.ATR]
    if any(pd.isna(v) for v in needed):
        return "ESPERAR"
    if (row.Close > row.SMA30 and row.Close > row.SMA10
            and RSI_LONG[0] < row.RSI < RSI_LONG[1] and row.MACD > row.Senal):
        return "LARGO"
    if (row.Close < row.SMA30 and row.Close < row.SMA10
            and RSI_SHORT[0] < row.RSI < RSI_SHORT[1] and row.MACD < row.Senal):
        return "CORTO"
    return "ESPERAR"


def levels(price: float, atr: float, direction: str):
    """(stop_loss, take_profit) para una entrada al precio dado."""
    if direction == "LARGO":
        return price - SL_ATR * atr, price + TP_ATR * atr
    return price + SL_ATR * atr, price - TP_ATR * atr


def position_size(capital: float, atr: float) -> float:
    """Unidades tales que tocar el SL pierda RISK_PCT del capital."""
    return capital * RISK_PCT / (SL_ATR * atr)


def holding_estimate(price: float, tp: float, atr: float, unidad: str) -> str:
    n = math.ceil(abs(tp - price) / atr)
    return f"{n} a {n + 2} {unidad}"


@dataclass
class Trade:
    fecha_entrada: pd.Timestamp
    direccion: str
    entrada: float
    sl: float
    tp: float
    unidades: float
    fecha_salida: pd.Timestamp | None = None
    salida: float | None = None
    motivo: str = ""
    resultado: float = 0.0


@dataclass
class BacktestResult:
    trades: list = field(default_factory=list)
    equity: pd.Series | None = None

    def metrics(self, capital: float) -> dict:
        cerrados = [t for t in self.trades if t.fecha_salida is not None]
        wins = [t for t in cerrados if t.resultado > 0]
        losses = [t for t in cerrados if t.resultado <= 0]
        neto = sum(t.resultado for t in cerrados)
        dd = 0.0
        if self.equity is not None and len(self.equity):
            dd = float((self.equity - self.equity.cummax()).min())
        return {
            "operaciones": len(cerrados),
            "ganadoras": len(wins),
            "perdedoras": len(losses),
            "acierto": len(wins) / len(cerrados) if cerrados else 0.0,
            "neto": neto,
            "neto_pct": neto / capital if capital else 0.0,
            "gan_media": sum(t.resultado for t in wins) / len(wins) if wins else 0.0,
            "perd_media": sum(t.resultado for t in losses) / len(losses) if losses else 0.0,
            "max_drawdown": dd,
        }


def backtest(df: pd.DataFrame, capital: float = 5000.0) -> BacktestResult:
    """Simula la estrategia sobre velas ya cerradas con indicadores.

    Misma mecánica que la pestaña Backtest del Google Sheet: la señal se
    evalúa al cierre de la vela i; la entrada es la apertura de la vela i+1;
    dentro de cada vela se comprueba SL primero (conservador), luego TP,
    luego cruce de la SMA10 al cierre. Riesgo fijo 1% del capital inicial
    (sin interés compuesto), sin costos de transacción.
    """
    res = BacktestResult()
    open_trade: Trade | None = None
    eq = []
    eq_idx = []
    acumulado = capital
    prev_signal = "ESPERAR"
    prev_atr = float("nan")

    for i in range(len(df)):
        row = df.iloc[i]
        fecha = df.index[i]
        # 1) gestionar posición abierta durante esta vela
        if open_trade is not None:
            t = open_trade
            motivo, precio = "", None
            if t.direccion == "LARGO":
                if row.Low <= t.sl:
                    motivo, precio = "SL", t.sl
                elif row.High >= t.tp:
                    motivo, precio = "TP", t.tp
                elif not pd.isna(row.SMA10) and row.Close < row.SMA10:
                    motivo, precio = "Cruce SMA10", row.Close
            else:
                if row.High >= t.sl:
                    motivo, precio = "SL", t.sl
                elif row.Low <= t.tp:
                    motivo, precio = "TP", t.tp
                elif not pd.isna(row.SMA10) and row.Close > row.SMA10:
                    motivo, precio = "Cruce SMA10", row.Close
            if motivo:
                t.fecha_salida, t.salida, t.motivo = fecha, precio, motivo
                signo = 1 if t.direccion == "LARGO" else -1
                t.resultado = (precio - t.entrada) * signo * t.unidades
                acumulado += t.resultado
                open_trade = None
        # 2) abrir posición si la vela anterior dio señal y estamos planos
        elif prev_signal in ("LARGO", "CORTO") and not pd.isna(prev_atr):
            entrada = float(row.Open)
            sl, tp = levels(entrada, prev_atr, prev_signal)
            open_trade = Trade(
                fecha_entrada=fecha, direccion=prev_signal, entrada=entrada,
                sl=sl, tp=tp, unidades=position_size(capital, prev_atr))
            res.trades.append(open_trade)
            # la vela de entrada también puede sacar la posición
            t = open_trade
            motivo, precio = "", None
            if t.direccion == "LARGO":
                if row.Low <= t.sl:
                    motivo, precio = "SL", t.sl
                elif row.High >= t.tp:
                    motivo, precio = "TP", t.tp
            else:
                if row.High >= t.sl:
                    motivo, precio = "SL", t.sl
                elif row.Low <= t.tp:
                    motivo, precio = "TP", t.tp
            if motivo:
                t.fecha_salida, t.salida, t.motivo = fecha, precio, motivo
                signo = 1 if t.direccion == "LARGO" else -1
                t.resultado = (precio - t.entrada) * signo * t.unidades
                acumulado += t.resultado
                open_trade = None
        prev_signal = signal(row)
        prev_atr = row.ATR
        eq.append(acumulado)
        eq_idx.append(fecha)

    res.equity = pd.Series(eq, index=eq_idx, name="Capital")
    return res


# ====================================================================
# ASESOR DE SALIDA para posiciones reales del usuario
# ====================================================================

def initial_levels_at(df: pd.DataFrame, entry_date, entry_price: float,
                      direction: str):
    """SL/TP según el ATR de la última vela cerrada antes de la entrada."""
    prev = df[df.index <= pd.Timestamp(entry_date)]
    ref = prev.iloc[-1] if len(prev) else df.iloc[-1]
    atr = float(ref.ATR) if not pd.isna(ref.ATR) else float(df["ATR"].dropna().iloc[-1])
    sl, tp = levels(entry_price, atr, direction)
    return sl, tp, atr


def evaluate_position(pos: dict, df: pd.DataFrame, live_price: float) -> dict:
    """Evalúa una posición abierta contra la última vela CERRADA y el precio
    vivo. Devuelve recomendación jerárquica basada en las reglas de la
    estrategia + trailing stop de 1.5×ATR para dejar correr la ganancia.

    Nota honesta: ningún sistema conoce el punto de MÁXIMA ganancia por
    adelantado; el trailing stop es la aproximación práctica — captura la
    mayor parte de la tendencia y cede el último tramo a cambio de no
    devolver todo en un giro.
    """
    last = df.iloc[-1]
    d, e, u = pos["direccion"], float(pos["entrada"]), float(pos["unidades"])
    sl, tp = float(pos["sl"]), float(pos["tp"])
    atr, sma10, rsi = float(last.ATR), float(last.SMA10), float(last.RSI)
    sign = 1 if d == "LARGO" else -1
    pnl = (live_price - e) * sign * u
    pnl_pct = (live_price - e) / e * sign
    profit_atr = (live_price - e) * sign / atr if atr else 0.0

    # trailing stop sugerido (solo se mueve a favor, nunca en contra)
    if d == "LARGO":
        trail = max(sl, float(last.Close) - SL_ATR * atr)
    else:
        trail = min(sl, float(last.Close) + SL_ATR * atr)

    if (d == "LARGO" and live_price <= sl) or (d == "CORTO" and live_price >= sl):
        rec, motivo = "🔴 CERRAR YA", "Stop loss alcanzado. Salir protege el capital; no esperar rebotes."
    elif (d == "LARGO" and live_price >= tp) or (d == "CORTO" and live_price <= tp):
        rec, motivo = "🟢 TOMAR GANANCIA", "Objetivo 3×ATR alcanzado (ratio 1:2 cumplido). Cerrar, o cerrar la mitad y dejar el resto con el trailing stop."
    elif (d == "LARGO" and float(last.Close) < sma10) or (d == "CORTO" and float(last.Close) > sma10):
        rec, motivo = "🟠 CERRAR", "Salida técnica: la última vela cerrada cruzó la SMA10 en contra. La tendencia que justificaba la posición se enfrió."
    elif profit_atr >= 2 and ((d == "LARGO" and rsi >= 70) or (d == "CORTO" and rsi <= 30)):
        rec, motivo = "🟡 ASEGURAR", f"Ganancia de {profit_atr:.1f}×ATR con RSI extremo ({rsi:.0f}): movimiento estirado. Sube el stop al trailing (${trail:,.2f}) o toma parcial."
    elif profit_atr >= 1:
        rec, motivo = "🟢 MANTENER", f"Tendencia intacta y ganancia ≥1×ATR. Mueve el stop a ${trail:,.2f} (trailing 1.5×ATR) para asegurar sin cortar el recorrido."
    else:
        rec, motivo = "🟢 MANTENER", "Tendencia intacta, sin condición de salida. El SL original sigue vigente; dejar trabajar la posición."

    return {"recomendacion": rec, "motivo": motivo, "pnl": pnl,
            "pnl_pct": pnl_pct, "profit_atr": profit_atr,
            "stop_sugerido": trail, "sl": sl, "tp": tp,
            "vela_cerrada": df.index[-1]}


# ====================================================================
# COMPARADOR de instrumentos (backtest multi-ticker)
# ====================================================================

def compare_instruments(datasets: dict, timeframe: str,
                        capital: float = 5000.0,
                        recent_months: int = 12) -> pd.DataFrame:
    """datasets: {ticker: daily DataFrame}. Corre el backtest completo y el
    de los últimos `recent_months` meses para cada ticker y devuelve una
    tabla ordenada por resultado reciente."""
    rows = []
    for tk, daily in datasets.items():
        candles = resample_ohlc(daily, timeframe)
        candles = drop_open_candle(candles, pd.Timestamp.now(tz="UTC"))
        df = add_indicators(candles).dropna(subset=["ATR"])
        if len(df) < 40:
            continue
        full = backtest(df, capital).metrics(capital)
        corte = df.index[-1] - pd.DateOffset(months=recent_months)
        df_rec = df[df.index >= corte]
        rec = backtest(df_rec, capital).metrics(capital) if len(df_rec) > 10 else full
        pf = (full["gan_media"] * full["ganadoras"]) / abs(
            full["perd_media"] * full["perdedoras"]) if full["perdedoras"] and full["perd_media"] else float("nan")
        rows.append({
            "Ticker": tk, "Ops": full["operaciones"],
            "Acierto": full["acierto"], "Neto %": full["neto_pct"],
            f"Neto % {recent_months}m": rec["neto_pct"],
            "Profit factor": pf,
            "Máx DD %": full["max_drawdown"] / capital,
        })
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values(f"Neto % {recent_months}m", ascending=False)
    return out


# ====================================================================
# PATRONES CHARTISTAS (heurísticos, sobre pivotes)
# ====================================================================

def _pivots(df: pd.DataFrame, window: int = 3):
    """Pivotes locales: (posición, 'H'/'L', precio), alternados."""
    highs, lows = df["High"].values, df["Low"].values
    raw = []
    for i in range(window, len(df) - window):
        if highs[i] >= highs[i - window:i + window + 1].max():
            raw.append((i, "H", float(highs[i])))
        if lows[i] <= lows[i - window:i + window + 1].min():
            raw.append((i, "L", float(lows[i])))
    raw.sort(key=lambda p: p[0])
    out = []
    for p in raw:
        if out and out[-1][1] == p[1]:
            if (p[1] == "H" and p[2] >= out[-1][2]) or \
               (p[1] == "L" and p[2] <= out[-1][2]):
                out[-1] = p
        else:
            out.append(p)
    return out


def detect_patterns(df: pd.DataFrame, lookback: int = 120,
                    tol: float = 0.05, pivot_window: int = 3) -> list:
    """Detección heurística de patrones clásicos en las últimas `lookback`
    velas: hombro-cabeza-hombro (y su inverso), doble techo/suelo,
    banderas/gallardetes y triángulos. Devuelve lista de dicts, del más
    reciente al más antiguo. Educativo: los patrones chartistas son
    subjetivos y esta detección automática es una aproximación."""
    tail = df.iloc[-lookback:].copy()
    idx = tail.index
    close = tail["Close"].values
    atr = float(tail["ATR"].dropna().iloc[-1]) if "ATR" in tail else \
        float((tail["High"] - tail["Low"]).mean())
    piv = _pivots(tail, pivot_window)
    found = []

    def add(nombre, sesgo, estado, detalle, i0, i1, nivel=None):
        found.append({"patron": nombre, "sesgo": sesgo, "estado": estado,
                      "detalle": detalle, "desde": idx[i0], "hasta": idx[i1],
                      "nivel": nivel, "_pos": i1})

    # --- HCH y HCH invertido (secuencias de 5 pivotes) ---
    for j in range(len(piv) - 4):
        seq = piv[j:j + 5]
        tipos = "".join(p[1] for p in seq)
        if tipos == "HLHLH":
            h1, t1, h2, t2, h3 = (p[2] for p in seq)
            if h2 > h1 and h2 > h3 and abs(h1 - h3) <= tol * h2 \
                    and min(h1, h3) > max(t1, t2):
                neck = (t1 + t2) / 2
                conf = close[-1] < neck
                add("Hombro-Cabeza-Hombro", "bajista",
                    "CONFIRMADO (rompió el cuello)" if conf else "en formación",
                    f"Cabeza {h2:,.2f}, hombros {h1:,.2f}/{h3:,.2f}, "
                    f"cuello ≈ {neck:,.2f}. Objetivo teórico: "
                    f"{neck - (h2 - neck):,.2f}.",
                    seq[0][0], seq[4][0], neck)
        elif tipos == "LHLHL":
            l1, p1_, l2, p2_, l3 = (p[2] for p in seq)
            if l2 < l1 and l2 < l3 and abs(l1 - l3) <= tol * max(l1, l3) \
                    and max(l1, l3) < min(p1_, p2_):
                neck = (p1_ + p2_) / 2
                conf = close[-1] > neck
                add("HCH invertido", "alcista",
                    "CONFIRMADO (rompió el cuello)" if conf else "en formación",
                    f"Cabeza {l2:,.2f}, hombros {l1:,.2f}/{l3:,.2f}, "
                    f"cuello ≈ {neck:,.2f}. Objetivo teórico: "
                    f"{neck + (neck - l2):,.2f}.",
                    seq[0][0], seq[4][0], neck)

    # --- doble techo / doble suelo (secuencias de 3 pivotes) ---
    for j in range(len(piv) - 2):
        seq = piv[j:j + 3]
        tipos = "".join(p[1] for p in seq)
        if tipos == "HLH":
            h1, t, h2 = (p[2] for p in seq)
            if abs(h1 - h2) <= 0.03 * h1 and min(h1, h2) - t >= 0.03 * t:
                conf = close[-1] < t
                add("Doble techo", "bajista",
                    "CONFIRMADO (rompió el valle)" if conf else "en formación",
                    f"Techos {h1:,.2f}/{h2:,.2f}, valle {t:,.2f}.",
                    seq[0][0], seq[2][0], t)
        elif tipos == "LHL":
            l1, p_, l2 = (p[2] for p in seq)
            if abs(l1 - l2) <= 0.03 * l1 and p_ - max(l1, l2) >= 0.03 * p_:
                conf = close[-1] > p_
                add("Doble suelo", "alcista",
                    "CONFIRMADO (rompió el pico)" if conf else "en formación",
                    f"Suelos {l1:,.2f}/{l2:,.2f}, pico {p_:,.2f}.",
                    seq[0][0], seq[2][0], p_)

    # --- bandera / gallardete (asta fuerte + consolidación corta) ---
    POLE, CONS = 10, 6
    if len(tail) >= POLE + CONS + 1:
        pole_move = close[-CONS - 1] - close[-POLE - CONS]
        cons = tail.iloc[-CONS:]
        cons_range = float(cons["High"].max() - cons["Low"].min())
        drift = abs(close[-1] - close[-CONS])
        if abs(pole_move) >= 3 * atr and cons_range <= 0.5 * abs(pole_move) \
                and drift <= 0.4 * abs(pole_move):
            half = CONS // 2
            r1 = float(cons["High"].iloc[:half].max() - cons["Low"].iloc[:half].min())
            r2 = float(cons["High"].iloc[half:].max() - cons["Low"].iloc[half:].min())
            nombre = "Gallardete" if r1 > 1.3 * r2 else "Bandera"
            sesgo = "alcista" if pole_move > 0 else "bajista"
            add(f"{nombre} {sesgo}", sesgo, "en formación",
                f"Asta de {abs(pole_move):,.2f} ({abs(pole_move)/atr:.1f}×ATR) y "
                f"consolidación estrecha. Suele continuar en la dirección del "
                f"asta al romper.",
                len(tail) - POLE - CONS, len(tail) - 1)

    # --- triángulos (pendientes de máximos y mínimos, últimas 20 velas) ---
    win = min(20, len(tail))
    seg = tail.iloc[-win:]
    ph = [(p[0], p[2]) for p in _pivots(seg, 2) if p[1] == "H"]
    pl = [(p[0], p[2]) for p in _pivots(seg, 2) if p[1] == "L"]
    if len(ph) >= 2 and len(pl) >= 2:
        import numpy as np
        sh = np.polyfit([p[0] for p in ph], [p[1] for p in ph], 1)[0] / atr
        slo = np.polyfit([p[0] for p in pl], [p[1] for p in pl], 1)[0] / atr
        nombre = None
        if sh < -0.05 and slo > 0.05:
            nombre, sesgo = "Triángulo simétrico", "neutral (rompe hacia cualquier lado)"
        elif abs(sh) <= 0.05 and slo > 0.05:
            nombre, sesgo = "Triángulo ascendente", "alcista"
        elif sh < -0.05 and abs(slo) <= 0.05:
            nombre, sesgo = "Triángulo descendente", "bajista"
        if nombre:
            add(nombre, sesgo, "en formación",
                "Rango en contracción: suele anticipar un movimiento fuerte "
                "al romper. Esperar la ruptura con la vela cerrada.",
                len(tail) - win, len(tail) - 1)

    found.sort(key=lambda f: f["_pos"], reverse=True)
    for f in found:
        f.pop("_pos")
    return found[:4]


# ====================================================================
# LABORATORIO: estrategias parametrizables + optimizador con validación
# ====================================================================
from dataclasses import dataclass as _dc
import random as _random


@_dc
class StrategyConfig:
    """Parámetros de una estrategia. `tipo` define las reglas de entrada:
      - 'tendencia': la original (medias + banda RSI + MACD opcional)
      - 'cruce':     cruce simple de medias (fast sobre slow)
      - 'donchian':  ruptura del canal de N velas (trend-following clásico)
      - 'pullback':  compra del retroceso dentro de la tendencia
    La salida es común: SL/TP por ATR + cruce de la media rápida en contra.
    En 'pullback', rsi_low es el umbral de sobreventa para entrar."""
    tipo: str = "tendencia"
    sma_fast: int = 10
    sma_slow: int = 30
    rsi_low: float = 50.0
    rsi_high: float = 70.0
    usar_macd: bool = True
    donchian: int = 8
    sl_atr: float = 1.5
    tp_atr: float = 3.0
    riesgo: float = 0.01

    def etiqueta(self) -> str:
        base = {"tendencia": f"Tendencia SMA{self.sma_fast}/{self.sma_slow} RSI {self.rsi_low:.0f}-{self.rsi_high:.0f}" + (" +MACD" if self.usar_macd else ""),
                "cruce": f"Cruce SMA{self.sma_fast}/{self.sma_slow}",
                "donchian": f"Donchian {self.donchian} velas",
                "pullback": f"Pullback SMA{self.sma_slow} RSI<{self.rsi_low:.0f}"}[self.tipo]
        return f"{base} · SL {self.sl_atr}×ATR TP {self.tp_atr}×ATR"


def prepare(candles: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    df = candles.copy()
    c = df["Close"]
    df["SMAf"] = c.rolling(cfg.sma_fast).mean()
    df["SMAs"] = c.rolling(cfg.sma_slow).mean()
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["Senal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    delta = c.diff()
    g = delta.clip(lower=0.0)
    l = (-delta).clip(lower=0.0)
    ag = g.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    al = l.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    df["RSI"] = 100 - 100 / (1 + ag / al)
    df.loc[al == 0, "RSI"] = 100.0
    prev = c.shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(),
                    (df["Low"] - prev).abs()], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    df["DonH"] = df["High"].shift(1).rolling(cfg.donchian).max()
    df["DonL"] = df["Low"].shift(1).rolling(cfg.donchian).min()
    return df


def signal_cfg(row, cfg: StrategyConfig) -> str:
    base = [row.SMAf, row.SMAs, row.RSI, row.ATR]
    if any(pd.isna(v) for v in base):
        return "ESPERAR"
    t = cfg.tipo
    if t == "tendencia":
        macd_l = row.MACD > row.Senal if cfg.usar_macd else True
        macd_c = row.MACD < row.Senal if cfg.usar_macd else True
        if row.Close > row.SMAs and row.Close > row.SMAf and \
                cfg.rsi_low < row.RSI < cfg.rsi_high and macd_l:
            return "LARGO"
        if row.Close < row.SMAs and row.Close < row.SMAf and \
                (100 - cfg.rsi_high) < row.RSI < (100 - cfg.rsi_low) and macd_c:
            return "CORTO"
    elif t == "cruce":
        if row.Close > row.SMAf > row.SMAs:
            return "LARGO"
        if row.Close < row.SMAf < row.SMAs:
            return "CORTO"
    elif t == "donchian":
        if not pd.isna(row.DonH) and row.Close > row.DonH:
            return "LARGO"
        if not pd.isna(row.DonL) and row.Close < row.DonL:
            return "CORTO"
    elif t == "pullback":
        if row.Close > row.SMAs and row.RSI < cfg.rsi_low:
            return "LARGO"
        if row.Close < row.SMAs and row.RSI > cfg.rsi_high:
            return "CORTO"
    return "ESPERAR"


def backtest_cfg(df: pd.DataFrame, cfg: StrategyConfig,
                 capital: float = 5000.0) -> BacktestResult:
    """Mismo motor conservador que backtest(), con reglas parametrizadas."""
    res = BacktestResult()
    open_trade = None
    eq, eq_idx = [], []
    acumulado = capital
    prev_signal, prev_atr = "ESPERAR", float("nan")
    for i in range(len(df)):
        row = df.iloc[i]
        fecha = df.index[i]
        if open_trade is not None:
            t = open_trade
            motivo, precio = "", None
            if t.direccion == "LARGO":
                if row.Low <= t.sl:
                    motivo, precio = "SL", t.sl
                elif row.High >= t.tp:
                    motivo, precio = "TP", t.tp
                elif not pd.isna(row.SMAf) and row.Close < row.SMAf:
                    motivo, precio = "Cruce media", row.Close
            else:
                if row.High >= t.sl:
                    motivo, precio = "SL", t.sl
                elif row.Low <= t.tp:
                    motivo, precio = "TP", t.tp
                elif not pd.isna(row.SMAf) and row.Close > row.SMAf:
                    motivo, precio = "Cruce media", row.Close
            if motivo:
                t.fecha_salida, t.salida, t.motivo = fecha, precio, motivo
                s = 1 if t.direccion == "LARGO" else -1
                t.resultado = (precio - t.entrada) * s * t.unidades
                acumulado += t.resultado
                open_trade = None
        elif prev_signal in ("LARGO", "CORTO") and not pd.isna(prev_atr) and prev_atr > 0:
            entrada = float(row.Open)
            if prev_signal == "LARGO":
                sl, tp = entrada - cfg.sl_atr * prev_atr, entrada + cfg.tp_atr * prev_atr
            else:
                sl, tp = entrada + cfg.sl_atr * prev_atr, entrada - cfg.tp_atr * prev_atr
            unidades = capital * cfg.riesgo / (cfg.sl_atr * prev_atr)
            open_trade = Trade(fecha_entrada=fecha, direccion=prev_signal,
                               entrada=entrada, sl=sl, tp=tp, unidades=unidades)
            res.trades.append(open_trade)
            t = open_trade
            motivo, precio = "", None
            if t.direccion == "LARGO":
                if row.Low <= t.sl:
                    motivo, precio = "SL", t.sl
                elif row.High >= t.tp:
                    motivo, precio = "TP", t.tp
            else:
                if row.High >= t.sl:
                    motivo, precio = "SL", t.sl
                elif row.Low <= t.tp:
                    motivo, precio = "TP", t.tp
            if motivo:
                t.fecha_salida, t.salida, t.motivo = fecha, precio, motivo
                s = 1 if t.direccion == "LARGO" else -1
                t.resultado = (precio - t.entrada) * s * t.unidades
                acumulado += t.resultado
                open_trade = None
        prev_signal = signal_cfg(row, cfg)
        prev_atr = row.ATR
        eq.append(acumulado)
        eq_idx.append(fecha)
    res.equity = pd.Series(eq, index=eq_idx, name="Capital")
    return res


def _anualizado(neto_pct: float, idx) -> float:
    years = max((idx[-1] - idx[0]).days / 365.25, 0.25)
    return neto_pct / years


def evaluate_config(candles: pd.DataFrame, cfg: StrategyConfig,
                    capital: float = 5000.0, split: float = 0.7) -> dict:
    """Backtest con validación temporal: optimiza-mira el primer `split`
    del histórico (in-sample) y examina el resto (out-of-sample), datos
    que la búsqueda nunca usó para elegir parámetros. Si una configuración
    brilla in-sample y fracasa out-of-sample, es sobreajuste, no ventaja."""
    df = prepare(candles, cfg).dropna(subset=["ATR"])
    corte = int(len(df) * split)
    df_is, df_oos = df.iloc[:corte], df.iloc[corte:]
    m_is = backtest_cfg(df_is, cfg, capital).metrics(capital)
    m_oos = backtest_cfg(df_oos, cfg, capital).metrics(capital)
    m_full = backtest_cfg(df, cfg, capital).metrics(capital)
    a_is = _anualizado(m_is["neto_pct"], df_is.index) if len(df_is) > 5 else 0
    a_oos = _anualizado(m_oos["neto_pct"], df_oos.index) if len(df_oos) > 5 else 0
    sobre = a_is > 0.05 and (a_oos < 0.3 * a_is)
    return {"Estrategia": cfg.etiqueta(), "cfg": cfg,
            "Anual IS": a_is, "Anual OOS": a_oos,
            "Ops": m_full["operaciones"], "Acierto": m_full["acierto"],
            "Máx DD %": m_full["max_drawdown"] / capital,
            "Sobreajuste": "⚠️ sí" if sobre else "no"}


def random_configs(tipos: list, n: int, riesgo: float = 0.01,
                   seed: int = 42) -> list:
    rng = _random.Random(seed)
    cfgs = []
    for _ in range(n):
        tipo = rng.choice(tipos)
        lo = rng.choice([35, 40, 45, 50, 55])
        cfgs.append(StrategyConfig(
            tipo=tipo,
            sma_fast=rng.choice([5, 8, 10, 12, 15]),
            sma_slow=rng.choice([20, 25, 30, 40, 50]),
            rsi_low=lo, rsi_high=lo + rng.choice([15, 20, 25, 30]),
            usar_macd=rng.choice([True, False]),
            donchian=rng.choice([4, 6, 8, 10, 13]),
            sl_atr=rng.choice([1.0, 1.5, 2.0, 2.5]),
            tp_atr=rng.choice([2.0, 2.5, 3.0, 4.0, 5.0]),
            riesgo=riesgo))
    # dedupe
    seen, out = set(), []
    for c in cfgs:
        k = (c.tipo, c.sma_fast, c.sma_slow, c.rsi_low, c.rsi_high,
             c.usar_macd, c.donchian, c.sl_atr, c.tp_atr)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


# ====================================================================
# PATRONES v2: geometría operable + escáner histórico de fiabilidad
# ====================================================================

def detect_patterns_v2(df: pd.DataFrame, lookback: int = 120,
                       tol: float = 0.05, pivot_window: int = 3) -> list:
    """Como detect_patterns, pero cada patrón incluye su plan operativo:
    nivel_ruptura (dónde confirma), objetivo (proyección geométrica) e
    invalidacion (dónde el patrón queda negado = stop). Los triángulos no
    llevan plan (dirección ambigua hasta la ruptura). También marca
    recien_confirmado si la confirmación ocurrió en la ÚLTIMA vela."""
    tail = df.iloc[-lookback:].copy()
    idx = tail.index
    close = tail["Close"].values
    atr = float(tail["ATR"].dropna().iloc[-1]) if "ATR" in tail and \
        tail["ATR"].notna().any() else float((tail["High"] - tail["Low"]).mean())
    piv = _pivots(tail, pivot_window)
    found = []

    def cruz(nivel, alcista):
        """(confirmado_hoy, confirmado_antes) respecto al nivel."""
        hoy = close[-1] > nivel if alcista else close[-1] < nivel
        ayer = (close[-2] > nivel if alcista else close[-2] < nivel) \
            if len(close) > 1 else False
        return hoy, ayer

    def add(nombre, sesgo, detalle, i0, i1, ruptura, objetivo, invalidacion,
            alcista):
        hoy, ayer = cruz(ruptura, alcista)
        estado = "CONFIRMADO" if hoy else "en formación"
        found.append({"patron": nombre, "sesgo": sesgo, "estado": estado,
                      "detalle": detalle, "desde": idx[i0], "hasta": idx[i1],
                      "nivel": ruptura, "nivel_ruptura": ruptura,
                      "objetivo": objetivo, "invalidacion": invalidacion,
                      "alcista": alcista,
                      "recien_confirmado": hoy and not ayer, "_pos": i1})

    for j in range(len(piv) - 4):
        seq = piv[j:j + 5]
        tipos = "".join(p[1] for p in seq)
        if tipos == "HLHLH":
            h1, t1, h2, t2, h3 = (p[2] for p in seq)
            if h2 > h1 and h2 > h3 and abs(h1 - h3) <= tol * h2 \
                    and min(h1, h3) > max(t1, t2):
                neck = (t1 + t2) / 2
                add("Hombro-Cabeza-Hombro", "bajista",
                    f"Cabeza {h2:,.2f}, cuello ≈ {neck:,.2f}.",
                    seq[0][0], seq[4][0], neck, neck - (h2 - neck), h3, False)
        elif tipos == "LHLHL":
            l1, p1_, l2, p2_, l3 = (p[2] for p in seq)
            if l2 < l1 and l2 < l3 and abs(l1 - l3) <= tol * max(l1, l3) \
                    and max(l1, l3) < min(p1_, p2_):
                neck = (p1_ + p2_) / 2
                add("HCH invertido", "alcista",
                    f"Cabeza {l2:,.2f}, cuello ≈ {neck:,.2f}.",
                    seq[0][0], seq[4][0], neck, neck + (neck - l2), l3, True)

    for j in range(len(piv) - 2):
        seq = piv[j:j + 3]
        tipos = "".join(p[1] for p in seq)
        if tipos == "HLH":
            h1, t, h2 = (p[2] for p in seq)
            if abs(h1 - h2) <= 0.03 * h1 and min(h1, h2) - t >= 0.03 * t:
                add("Doble techo", "bajista",
                    f"Techos {h1:,.2f}/{h2:,.2f}, valle {t:,.2f}.",
                    seq[0][0], seq[2][0], t, t - ((h1 + h2) / 2 - t),
                    max(h1, h2), False)
        elif tipos == "LHL":
            l1, p_, l2 = (p[2] for p in seq)
            if abs(l1 - l2) <= 0.03 * l1 and p_ - max(l1, l2) >= 0.03 * p_:
                add("Doble suelo", "alcista",
                    f"Suelos {l1:,.2f}/{l2:,.2f}, pico {p_:,.2f}.",
                    seq[0][0], seq[2][0], p_, p_ + (p_ - (l1 + l2) / 2),
                    min(l1, l2), True)

    POLE, CONS = 10, 6
    if len(tail) >= POLE + CONS + 1:
        pole_move = close[-CONS - 1] - close[-POLE - CONS]
        cons = tail.iloc[-CONS:]
        hi, lo = float(cons["High"].max()), float(cons["Low"].min())
        drift = abs(close[-1] - close[-CONS])
        if abs(pole_move) >= 3 * atr and (hi - lo) <= 0.5 * abs(pole_move) \
                and drift <= 0.4 * abs(pole_move):
            half = CONS // 2
            r1 = float(cons["High"].iloc[:half].max() - cons["Low"].iloc[:half].min())
            r2 = float(cons["High"].iloc[half:].max() - cons["Low"].iloc[half:].min())
            nombre = "Gallardete" if r1 > 1.3 * r2 else "Bandera"
            alc = pole_move > 0
            add(f"{nombre} {'alcista' if alc else 'bajista'}",
                "alcista" if alc else "bajista",
                f"Asta de {abs(pole_move)/atr:.1f}×ATR y consolidación "
                f"{lo:,.2f}-{hi:,.2f}.",
                len(tail) - POLE - CONS, len(tail) - 1,
                hi if alc else lo,
                (hi + abs(pole_move)) if alc else (lo - abs(pole_move)),
                lo if alc else hi, alc)

    win = min(20, len(tail))
    seg = tail.iloc[-win:]
    ph = [(p[0], p[2]) for p in _pivots(seg, 2) if p[1] == "H"]
    pl = [(p[0], p[2]) for p in _pivots(seg, 2) if p[1] == "L"]
    if len(ph) >= 2 and len(pl) >= 2:
        import numpy as np
        sh = np.polyfit([p[0] for p in ph], [p[1] for p in ph], 1)[0] / atr
        slo = np.polyfit([p[0] for p in pl], [p[1] for p in pl], 1)[0] / atr
        nombre = sesgo = None
        if sh < -0.05 and slo > 0.05:
            nombre, sesgo = "Triángulo simétrico", "neutral"
        elif abs(sh) <= 0.05 and slo > 0.05:
            nombre, sesgo = "Triángulo ascendente", "alcista"
        elif sh < -0.05 and abs(slo) <= 0.05:
            nombre, sesgo = "Triángulo descendente", "bajista"
        if nombre:
            found.append({"patron": nombre, "sesgo": sesgo,
                          "estado": "en formación",
                          "detalle": "Rango en contracción; sin plan "
                          "operativo hasta la ruptura (dirección ambigua).",
                          "desde": idx[len(tail) - win],
                          "hasta": idx[len(tail) - 1], "nivel": None,
                          "nivel_ruptura": None, "objetivo": None,
                          "invalidacion": None, "alcista": None,
                          "recien_confirmado": False,
                          "_pos": len(tail) - 1})

    found.sort(key=lambda f: f["_pos"], reverse=True)
    for f in found:
        f.pop("_pos")
    return found[:5]


def scan_pattern_events(df: pd.DataFrame, lookback: int = 60,
                        horizon: int = 25, pivot_window: int = 3) -> list:
    """Recorre el histórico vela a vela usando SOLO información disponible
    hasta ese momento (sin mirar el futuro). Dispara un evento cuando un
    patrón confirma: (a) el cierre de la vela actual cruza el nivel de un
    patrón en formación, o (b) el patrón aparece recién confirmado en la
    ventana (la maduración del pivote llegó una vela tarde). Entrada en la
    apertura siguiente a la confirmación, stop en la invalidación, objetivo
    geométrico; SL prioritario si ambos se tocan en la misma vela
    (conservador); timeout a `horizon` velas cerrando a mercado."""
    events = []
    last_fire = {}
    for t in range(25, len(df) - 1):
        win = df.iloc[max(0, t - lookback):t]
        if len(win) < 20:
            continue
        pats = detect_patterns_v2(win, lookback=len(win),
                                  pivot_window=pivot_window)
        row = df.iloc[t]
        for p in pats:
            nr = p.get("nivel_ruptura")
            if nr is None:
                continue
            alc = p["alcista"]
            if p["estado"] == "CONFIRMADO":
                if not p.get("recien_confirmado"):
                    continue
                ini_i = t              # confirmó en t-1; entramos en t
            else:
                if not (row.Close > nr if alc else row.Close < nr):
                    continue
                ini_i = t + 1          # confirma en t; entramos en t+1
            key = p["patron"]
            if key in last_fire and t - last_fire[key] < 5:
                continue
            last_fire[key] = t
            if ini_i >= len(df):
                continue
            entrada = float(df.iloc[ini_i].Open)
            stop, obj = float(p["invalidacion"]), float(p["objetivo"])
            if (alc and entrada <= stop) or (not alc and entrada >= stop):
                continue  # gap que ya nego el patron
            salida, motivo = None, "timeout"
            fin_i = min(ini_i + horizon, len(df))
            for i in range(ini_i, fin_i):
                c = df.iloc[i]
                if alc:
                    if c.Low <= stop:
                        salida, motivo = stop, "stop"
                        break
                    if c.High >= obj:
                        salida, motivo = obj, "objetivo"
                        break
                else:
                    if c.High >= stop:
                        salida, motivo = stop, "stop"
                        break
                    if c.Low <= obj:
                        salida, motivo = obj, "objetivo"
                        break
            if salida is None:
                salida = float(df.iloc[fin_i - 1].Close)
            riesgo_u = abs(entrada - stop)
            if riesgo_u <= 0:
                continue
            r_mult = ((salida - entrada) if alc else (entrada - salida)) / riesgo_u
            events.append({"fecha": df.index[t], "patron": p["patron"],
                           "sesgo": "alcista" if alc else "bajista",
                           "entrada": entrada, "stop": stop, "objetivo": obj,
                           "salida": salida, "motivo": motivo,
                           "R": r_mult})
    return events


def pattern_reliability(df: pd.DataFrame, split: float = 0.7,
                        capital: float = 5000.0, riesgo: float = 0.005,
                        pivot_window: int = 3) -> dict:
    """Fiabilidad histórica de los patrones con validación temporal:
    resume el cumplimiento en el primer 70% (análisis) y por separado en
    el 30% final (examen). Devuelve {'eventos', 'resumen', 'equity',
    'metricas'} — la equity asume riesgo fijo `riesgo` del capital por
    evento (Estrategia B, la agresiva)."""
    events = scan_pattern_events(df, pivot_window=pivot_window)
    corte = df.index[int(len(df) * split)]
    rows = []
    for patron in sorted({e["patron"] for e in events}):
        evs = [e for e in events if e["patron"] == patron]
        eis = [e for e in evs if e["fecha"] < corte]
        eoos = [e for e in evs if e["fecha"] >= corte]

        def cumpl(lst):
            return sum(1 for e in lst if e["motivo"] == "objetivo") / len(lst) \
                if lst else float("nan")
        rows.append({"Patrón": patron, "Eventos": len(evs),
                     "Cumplimiento": cumpl(evs),
                     "R medio": sum(e["R"] for e in evs) / len(evs),
                     "Ev. IS": len(eis), "Cumpl. IS": cumpl(eis),
                     "Ev. OOS": len(eoos), "Cumpl. OOS": cumpl(eoos)})
    resumen = pd.DataFrame(rows)
    eq_val, eq_idx, acc = [], [], capital
    for e in sorted(events, key=lambda x: x["fecha"]):
        acc += e["R"] * capital * riesgo
        eq_val.append(acc)
        eq_idx.append(e["fecha"])
    equity = pd.Series(eq_val, index=eq_idx, name="Capital")
    total_r = sum(e["R"] for e in events)
    metricas = {"eventos": len(events),
                "cumplimiento": sum(1 for e in events
                                    if e["motivo"] == "objetivo") / len(events)
                if events else 0.0,
                "r_total": total_r,
                "neto_pct": total_r * riesgo,
                "corte": corte}
    return {"eventos": events, "resumen": resumen, "equity": equity,
            "metricas": metricas}
