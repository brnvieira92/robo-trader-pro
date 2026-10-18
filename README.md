# 🤖 RoboTrader Pro — Plataforma Unificada de Trading Automatizado (Safirion + MetaTrader 5 + IA)

O **RoboTrader Pro** é um sistema completo de análise técnica quantitativa e **Machine Learning (`RandomForestClassifier`)** em tempo real, com execução automática e manual para:

1. **Opções Binárias, Digital, Blitz e OTC** na corretora **Safirion** (`https://trade.safirion.com/traderoom`), operando 100% em segundo plano (mesmo com a janela do navegador minimizada) via Ponte WebSocket + Extensão Chrome Manifest V3.
2. **B3 (Mini Índice `WIN$N` e Mini Dólar `WDO$N`)** e **Forex (`EURUSD`, `GBPUSD`, `USDJPY`, `XAUUSD`)** via integração direta com o terminal **MetaTrader 5 (MT5)**.

---

## 🚀 Principais Recursos

- **Painel Web Local em Tempo Real (`FastAPI` + `TradingView Lightweight Charts`)**:
  - Gráfico de velas (Candlesticks) sincronizado com os preços reais da corretora.
  - Plotagem ao vivo de **EMA 9**, **EMA 21**, **Bandas de Bollinger (20, 2.0)**, **RSI (14)**, **ATR (14)**, **MACD** e **Probabilidades da IA (`▲ % | ▼ %`)**.
- **Ponte Safirion em 2º Plano (`safirion-extension/`)**:
  - Interceptação nativa de WebSockets (`page_ws_hook.js`) com filtro rigoroso anti-ruído (`<= 0.12%` por tick) para impedir contaminação de outros ativos.
  - Leitura automática do Saldo Real (`R$`) e auditoria real de `WIN` / `LOSS` sincronizada com a virada exata do relógio (`:00`).
  - **Auto-Destravamento do botão `+ NOVA OPÇÃO`**: clica automaticamente nas propriedades internas do React (`__reactProps` / `__reactFiber`) assim que a operação encerra, reabilitando os botões `CALL` e `PUT` mesmo com o navegador minimizado.
- **Motor Híbrido de Estratégias + Inteligência Artificial (`scikit-learn`)**:
  1. **Bandas de Bollinger + RSI Híbrido**: Opera Reversão com Pavio de Rejeição nos extremos, Fluxo de Tendência (*Walking the Bands*) em rompimentos fortes e Pullback Dinâmico na EMA 9 (equilibrando entradas `CALL` e `PUT`).
  2. **Cruzamento de Médias (EMA 9/21) + Filtro RSI**: Seguimento de tendência.
  3. **Rompimento de Price Action (Donchian Breakout) + ATR**: Explosão de volatilidade.
  4. **Multi-Confluência Pro**: Combinação ponderada de EMA + RSI + Bollinger + MACD.
  5. **Machine Learning (`RandomForestClassifier` - IA)**: Modelo com 45 árvores de decisão treinado em tempo real a cada vela sobre 10 variáveis de microestrutura, com **Aprendizado por Reforço** nos resultados reais (`WIN`/`LOSS`).

---

## 📂 Estrutura do Projeto

```text
robo-trader-pro/
├── backend/
│   ├── main.py                 # Servidor FastAPI, rotas REST e WebSocket (/ws/safirion-bridge)
│   ├── bot_manager.py          # Orquestrador central, relógio de expiração real e gestão de risco
│   ├── strategies.py           # 5 motores de estratégia + Juiz de IA em segundo plano
│   ├── ml_engine.py            # Motor de Machine Learning (RandomForestClassifier + memória de reforço)
│   ├── safirion_connector.py   # Conector WebSocket/DOM para Safirion (Binárias, Digital, Blitz, OTC)
│   └── mt5_connector.py        # Conector nativo MetaTrader 5 (B3 e Forex) com fallback simulado
├── frontend/
│   └── index.html              # Dashboard Web Dark Mode (TailwindCSS + Lightweight Charts)
├── safirion-extension/
│   ├── manifest.json           # Extensão Chrome Manifest V3
│   ├── page_ws_hook.js         # Hook de WebSocket e executor React Fiber no mundo MAIN
│   └── content_bridge.js       # Ponte DOM/WebSocket e HUD flutuante na Safirion
├── iniciar_robo.bat            # Inicializador rápido para Windows
└── requirements.txt            # Dependências Python
```

---

## 🛠️ Como Instalar e Executar

### 1. Pré-requisitos
- **Python 3.10+** instalado no Windows.
- **Google Chrome** (para operar na Safirion) e/ou **MetaTrader 5** (para operar B3/Forex).

### 2. Instalação Rápida
Basta dar dois cliques em **`iniciar_robo.bat`** ou executar no terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Depois, abra o painel no navegador em:
👉 **`http://localhost:8000`**

### 3. Configurando a Extensão da Safirion no Chrome
1. No Chrome, acesse `chrome://extensions/`.
2. Ative a chave **"Modo do desenvolvedor"** (canto superior direito).
3. Clique em **"Carregar sem compactação"** e selecione a pasta `safirion-extension` deste projeto.
4. Abra `https://trade.safirion.com/traderoom`, clique em **`1. Vincular CALL ▲`** e depois no botão Verde da corretora; clique em **`2. Vincular PUT ▼`** e depois no botão Vermelho da corretora.
5. No painel `http://localhost:8000`, clique em **"LIGAR ROBÔ AUTOMÁTICO"**.
