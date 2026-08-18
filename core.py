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
