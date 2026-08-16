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
