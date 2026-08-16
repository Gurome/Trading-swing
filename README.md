# 🚦 Semáforo Swing — App de la estrategia en Python

Réplica en Python de la plantilla de Google Sheets, con datos de **Yahoo
Finance** (se consolidan poco después del cierre de NY, sin la fragilidad
de GOOGLEFINANCE). Incluye los 3 marcos temporales (semanal, quincenal,
diario), varios instrumentos (BABA, QQQ, SPY, MSFT, AAPL, GLD), semáforo,
SL/TP, tamaño de posición, tiempo estimado y backtest con curva de capital.

## Archivos
- `app.py` — interfaz (Streamlit, mobile-first)
- `core.py` — lógica pura: indicadores, señal y backtest (testeada)
- `requirements.txt` — dependencias

## Opción A — Desplegar gratis en la nube (recomendada, ~10 min una vez)

1. Crea una cuenta en **github.com** (si no tienes).
2. Crea un repositorio nuevo (puede ser privado) llamado p. ej.
   `semaforo-swing` y sube los 3 archivos (botón "Add file → Upload files").
3. Entra a **share.streamlit.io** e inicia sesión con tu cuenta de GitHub.
4. "Create app" → elige tu repositorio → archivo principal `app.py` →
   **Deploy**. En ~2 minutos tendrás una URL pública tipo
   `https://tuusuario-semaforo-swing.streamlit.app`.
5. En el celular: abre esa URL en el navegador → menú → **"Añadir a
   pantalla de inicio"**. Queda como una app con icono.

Notas del plan gratuito: si nadie la abre en varios días la app "duerme";
al abrirla despierta sola en ~30-60 segundos. Los datos se refrescan cada
15 minutos como máximo (caché) y al recargar la página.

## Opción B — Correr en tu computadora

```bash
pip install -r requirements.txt
streamlit run app.py
```

Se abre en el navegador (http://localhost:8501). En el celular de la misma
red WiFi: usa la IP que muestra la terminal (Network URL).

## Rutina (idéntica a la de las hojas)

- **Semanal/Quincenal:** abrir la app el fin de semana, leer el semáforo,
  ejecutar el lunes en la apertura con el SL/TP/tamaño mostrados.
- **Diario:** abrir después de las 4:00 pm hora CDMX (la app ignora
  automáticamente la vela en formación), ejecutar a la mañana siguiente.
- Filtro de gap: si abre >±3-4% del cierre, descartar la señal.

## Reglas implementadas (mismas que el Google Sheet)

- 🟢 LARGO: Cierre>SMA30 y Cierre>SMA10 y RSI 50-70 y MACD>Señal.
- 🔴 CORTO: espejo (RSI 30-50).
- SL = ±1.5×ATR(14) · TP = ±3×ATR (ratio 1:2) · riesgo 1% del capital.
- Salida técnica: cierre cruzando la SMA10 en contra.
- Backtest: entrada en la apertura siguiente a la señal; si SL y TP se
  tocan en la misma vela, se asume SL (conservador); sin spread ni
  comisiones — la realidad será algo peor.

## Advertencias

- Herramienta educativa, **no asesoría financiera**.
- Yahoo Finance: precio "vivo" con retraso ~15 min; los históricos
  diarios se consolidan tras el cierre. Puede haber caídas puntuales del
  servicio (la app muestra el error y basta reintentar).
- Riesgos del instrumento (gaps de BABA, comisiones de CFD en eToro) y de
  la estrategia (señales falsas en rangos) aplican igual que siempre.
