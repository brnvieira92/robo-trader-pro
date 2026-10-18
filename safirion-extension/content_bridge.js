/**
 * RoboTrader Pro — Safirion Bridge v4.6
 * - Altera o valor no campo "Invest (R$)" clicando automaticamente nos botões [+] e [-] da Safirion!
 * - Clica automaticamente no botão "+ NOVA OPÇÃO" assim que a operação encerra (mesmo com tela minimizada).
 * - Lê a Banca Real e sincroniza com a Gestão de Banca IA (1x, 2x, 3x, 4x + Martingale Inteligente).
 */

(function () {
  if (window.__ROBO_TRADER_BRIDGE_SOCKET__) {
    try { window.__ROBO_TRADER_BRIDGE_SOCKET__.close(); } catch (e) {}
  }
  const oldHud = document.getElementById("rt-safirion-hud");
  if (oldHud) oldHud.remove();

  const BRIDGE_WS_URL = "ws://127.0.0.1:8000/ws/safirion-bridge";
  let bridgeSocket = null;
  let isConnectingWs = false;
  let lastValidPrice = null;
  let lastDetectedBalance = null;
  let activeAssetId = null;
  let detectedSymbol = "EURUSD-OTC";
  let detectedMode = "BINARY";
  let mappingTarget = null;

  // Controle de Auto-Sincronia Contínua (Watchdog de Ticks e WebSocket)
  let lastTickSentAtMs = Date.now();
  let lastServerPongAtMs = Date.now();
  let outlierStreakCount = 0;
  let outlierLastCandidate = null;

  // Controle de destravamento do botão "+ NOVA OPÇÃO" e rastreamento do valor atual no campo "Invest"
  let needsNovaOpcaoUnlock = false;
  let tradeAutoUnlockAtMs = 0;
  let trackedInvestAmount = 5;

  let boundTargets = {
    CALL: null,
    PUT: null,
    INVEST: null,
    NOVA_OPCAO: null,
  };

  function saveBoundTargetsToStorage() {
    try {
      const serializable = {};
      for (const k of ["CALL", "PUT", "INVEST", "NOVA_OPCAO"]) {
        if (boundTargets[k]) {
          serializable[k] = {
            clientX: boundTargets[k].clientX,
            clientY: boundTargets[k].clientY,
            screenX: boundTargets[k].screenX,
            screenY: boundTargets[k].screenY,
            auto: Boolean(boundTargets[k].auto),
          };
        }
      }
      localStorage.setItem("rt_saved_targets_v51", JSON.stringify(serializable));
    } catch (e) {}
  }

  function restoreBoundTargetsFromStorage() {
    try {
      const raw = localStorage.getItem("rt_saved_targets_v51") || localStorage.getItem("rt_saved_targets_v49");
      if (!raw) return;
      const parsed = JSON.parse(raw);
      for (const k of ["CALL", "PUT", "INVEST", "NOVA_OPCAO"]) {
        if (parsed[k] && typeof parsed[k].clientX === "number" && typeof parsed[k].clientY === "number") {
          boundTargets[k] = {
            el: null,
            rawEl: null,
            clientX: parsed[k].clientX,
            clientY: parsed[k].clientY,
            screenX: parsed[k].screenX || parsed[k].clientX,
            screenY: parsed[k].screenY || parsed[k].clientY,
            auto: Boolean(parsed[k].auto),
          };
        }
      }
    } catch (e) {}
  }

  restoreBoundTargetsFromStorage();

  // Dispara cliques e duplo-clique (dblclick) diretamente nas props/Fiber do React
  function fireReactClick(node, cx, cy, isDbl) {
    if (!node || node === document.body || node === document.documentElement) return;
    function mkEv(det, btns, type) {
      return {
        bubbles: true,
        cancelable: true,
        defaultPrevented: false,
        isTrusted: true,
        view: window,
        detail: det,
        clientX: cx || 0,
        clientY: cy || 0,
        screenX: cx || 0,
        screenY: cy || 0,
        button: 0,
        buttons: btns,
        pointerId: 1,
        pointerType: "mouse",
        isPrimary: true,
        target: node,
        currentTarget: node,
        preventDefault: function () {},
        stopPropagation: function () {},
        persist: function () {},
        nativeEvent: new MouseEvent(type, {
          bubbles: true,
          cancelable: true,
          detail: det,
          clientX: cx || 0,
          clientY: cy || 0,
          button: 0,
        }),
      };
    }
    const evDown1 = mkEv(1, 1, "mousedown");
    const evUp1 = mkEv(1, 0, "mouseup");
    const evClick1 = mkEv(1, 0, "click");
    const evDown2 = mkEv(2, 1, "mousedown");
    const evUp2 = mkEv(2, 0, "mouseup");
    const evClick2 = mkEv(2, 0, "click");
    const evDbl = mkEv(2, 0, "dblclick");

    try {
      const keys = Object.keys(node);
      for (const k of keys) {
        if (k.startsWith("__reactProps") || k.startsWith("__reactEventHandlers")) {
          const p = node[k];
          if (p && typeof p === "object") {
            if (typeof p.onPointerDown === "function") try { p.onPointerDown(evDown1); } catch (e) {}
            if (typeof p.onMouseDown === "function") try { p.onMouseDown(evDown1); } catch (e) {}
            if (typeof p.onPointerUp === "function") try { p.onPointerUp(evUp1); } catch (e) {}
            if (typeof p.onMouseUp === "function") try { p.onMouseUp(evUp1); } catch (e) {}
            if (typeof p.onClick === "function") try { p.onClick(evClick1); } catch (e) {}
            if (isDbl) {
              if (typeof p.onMouseDown === "function") try { p.onMouseDown(evDown2); } catch (e) {}
              if (typeof p.onMouseUp === "function") try { p.onMouseUp(evUp2); } catch (e) {}
              if (typeof p.onClick === "function") try { p.onClick(evClick2); } catch (e) {}
              if (typeof p.onDoubleClick === "function") try { p.onDoubleClick(evDbl); } catch (e) {}
            }
          }
        } else if (k.startsWith("__reactFiber") || k.startsWith("__reactInternalInstance")) {
          let f = node[k];
          for (let d = 0; d < 6 && f; d++) {
            const mp = f.memoizedProps;
            if (mp && typeof mp === "object") {
              if (isDbl && typeof mp.onDoubleClick === "function") {
                try { mp.onDoubleClick(evDbl); } catch (e) {}
                break;
              } else if (typeof mp.onClick === "function" || typeof mp.onMouseDown === "function") {
                if (typeof mp.onMouseDown === "function") try { mp.onMouseDown(evDown1); } catch (e) {}
                if (typeof mp.onClick === "function") try { mp.onClick(evClick1); } catch (e) {}
                if (isDbl && typeof mp.onClick === "function") try { mp.onClick(evClick2); } catch (e) {}
                break;
              }
            }
            f = f.return;
          }
        }
      }
    } catch (e) {}
  }

  // Invoca handlers de teclado/change/input/blur diretamente nas props do React (__reactProps e __reactFiber)
  function invokeReactHandlersOnChain(startNode, handlerNames, eventPayload) {
    if (!startNode) return;
    const hud = document.getElementById("rt-safirion-hud");
    let curr = startNode;
    const seenNodes = new Set();

    for (let depth = 0; depth < 6 && curr && curr !== document.body; depth++) {
      if (hud && hud.contains(curr)) break;
      if (!seenNodes.has(curr)) {
        seenNodes.add(curr);
        try {
          const keys = Object.keys(curr);
          for (const k of keys) {
            if (k.startsWith("__reactProps") || k.startsWith("__reactEventHandlers")) {
              const props = curr[k];
              if (props && typeof props === "object") {
                for (const hName of handlerNames) {
                  if (typeof props[hName] === "function") {
                    try {
                      props[hName]({
                        ...eventPayload,
                        target: startNode,
                        currentTarget: curr,
                      });
                    } catch (e) {}
                  }
                }
              }
            } else if (k.startsWith("__reactFiber") || k.startsWith("__reactInternalInstance")) {
              let fiber = curr[k];
              for (let fDepth = 0; fDepth < 8 && fiber; fDepth++) {
                const mp = fiber.memoizedProps;
                if (mp && typeof mp === "object") {
                  for (const hName of handlerNames) {
                    if (typeof mp[hName] === "function") {
                      try {
                        mp[hName]({
                          ...eventPayload,
                          target: startNode,
                          currentTarget: curr,
                        });
                      } catch (e) {}
                    }
                  }
                }
                fiber = fiber.return;
              }
            }
          }
        } catch (e) {}
      }
      curr = curr.parentElement;
    }
  }

  // Atualiza um <input> controlado pelo React garantindo que o _valueTracker detecte a mudança!
  function forceReactInputValue(inputEl, newValueStr, lastChar) {
    const prevValue = inputEl.value;
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value"
    )?.set;

    const tracker = inputEl._valueTracker;
    if (tracker) {
      try {
        tracker.setValue(prevValue === newValueStr ? "" : prevValue);
      } catch (e) {}
    }

    if (nativeSetter) {
      nativeSetter.call(inputEl, newValueStr);
    } else {
      inputEl.value = newValueStr;
    }

    const inputEv = new InputEvent("input", {
      bubbles: true,
      cancelable: true,
      composed: true,
      data: lastChar || newValueStr,
      inputType: "insertText",
    });
    inputEl.dispatchEvent(inputEv);

    const changeEv = new Event("change", { bubbles: true, cancelable: true });
    inputEl.dispatchEvent(changeEv);

    invokeReactHandlersOnChain(inputEl, ["onInput", "onChange"], {
      bubbles: true,
      cancelable: false,
      defaultPrevented: false,
      isTrusted: true,
      type: "change",
      target: inputEl,
      currentTarget: inputEl,
      nativeEvent: inputEv,
      preventDefault: function () {},
      stopPropagation: function () {},
      persist: function () {},
    });
  }

  function cleanAlpha(str) {
    return (str || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-zA-Z]/g, "")
      .toUpperCase();
  }

  // Lê o valor atual que está dentro do campo "Invest (R$ 5)" na Safirion
  function readCurrentInvestAmountFromDom() {
    try {
      const hud = document.getElementById("rt-safirion-hud");
      // 1. Verifica <input> na barra lateral direita (x > 65% da tela, topo < 260px)
      const inputs = document.querySelectorAll("input");
      for (const inp of inputs) {
        if (hud && hud.contains(inp)) continue;
        const r = inp.getBoundingClientRect();
        if (r.left > window.innerWidth * 0.65 && r.top >= 20 && r.top < 260 && r.width > 5) {
          const raw = (inp.value || inp.getAttribute("value") || "").replace(/[^\d.,]/g, "").replace(",", ".");
          const val = parseFloat(raw);
          if (!isNaN(val) && val > 0) {
            trackedInvestAmount = val;
            return val;
          }
        }
      }

      // 2. Verifica elementos de texto no bloco "Invest" (topo direito entre Y=35 e Y=220)
      const els = document.querySelectorAll("div, span, label, p");
      for (const el of els) {
        if (hud && hud.contains(el)) continue;
        const r = el.getBoundingClientRect();
        if (r.left > window.innerWidth * 0.72 && r.top >= 35 && r.top <= 220 && r.width <= 190 && r.height <= 85) {
          const txt = (el.textContent || "").replace(/\s+/g, " ").trim();
          const m = txt.match(/^R\$\s*(\d+(?:[.,]\d+)?)$/i) || (txt.includes("Invest") ? txt.match(/R\$\s*(\d+(?:[.,]\d+)?)/i) : null);
          if (m) {
            const val = parseFloat(m[1].replace(",", "."));
            if (!isNaN(val) && val > 0 && val < 50000) {
              trackedInvestAmount = val;
              return val;
            }
          }
        }
      }
    } catch (e) {}
    return null;
  }

  // Lê o saldo real ("R$ 137.09") no cabeçalho da Safirion
  function readSafirionDomState() {
    let domBalance = lastDetectedBalance;
    let lastResultPnl = null;

    try {
      const hud = document.getElementById("rt-safirion-hud");
      const elements = document.querySelectorAll("div, span, button, p, a");

      for (const el of elements) {
        if (hud && hud.contains(el)) continue;
        const txt = (el.textContent || "").replace(/\s+/g, " ").trim();
        if (!txt || txt.length > 45) continue;

        const balMatch = txt.match(/^R\$\s*(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})$/);
        if (balMatch) {
          const r = el.getBoundingClientRect();
          if (r.top >= 0 && r.top < 95 && r.left > window.innerWidth * 0.45) {
            let numStr = balMatch[1];
            if (numStr.includes(",") && numStr.includes(".")) {
              numStr = numStr.replace(/\./g, "").replace(",", ".");
            } else if (numStr.includes(",")) {
              numStr = numStr.replace(",", ".");
            }
            const val = parseFloat(numStr);
            if (!isNaN(val) && val > 0) {
              domBalance = val;
              lastDetectedBalance = val;
            }
          }
        }

        if (txt.includes("RESULTADO (L/P)")) {
          const match = txt.match(/([+-]?\s*R\$\s*\d+([.,]\d{1,2})?)/);
          if (match) {
            const raw = match[1].replace(/\s+/g, "").replace("R$", "").replace(",", ".");
            const pnlVal = parseFloat(raw);
            if (!isNaN(pnlVal)) {
              lastResultPnl = pnlVal;
            }
          }
        }
      }
    } catch (e) {}

    return { domBalance, lastResultPnl };
  }

  function isStrictSameAssetTick(val) {
    if (typeof val !== "number" || !isFinite(val)) return false;
    if (Number.isInteger(val)) return false;
    if (val <= 0.0001 || val > 180000) return false;
    // Bloqueia constante estática de taxa de conversão interna da Safirion (1.12135) que distorcia as velas!
    if (Math.abs(val - 1.12135) < 1e-6) return false;

    if (lastValidPrice !== null) {
      const relDiff = Math.abs(val - lastValidPrice) / lastValidPrice;
      // Ticks reais variam menos de 0.035% (~3.5 pips) por pacote -> passam com ZERO atraso (0ms)!
      if (relDiff > 0.00035) {
        // Só reancora se forem 3 ticks reais em movimento (val != outlierLastCandidate) na mesma faixa nova
        if (
          outlierLastCandidate !== null &&
          Math.abs(val - outlierLastCandidate) > 1e-7 &&
          Math.abs(val - outlierLastCandidate) / Math.max(val, 1e-6) < 0.00030 &&
          relDiff < 0.020
        ) {
          outlierStreakCount++;
        } else {
          outlierStreakCount = 1;
        }
        outlierLastCandidate = val;

        if (outlierStreakCount >= 3) {
          lastValidPrice = val;
          outlierStreakCount = 0;
          return true;
        }
        return false;
      }
    }

    outlierStreakCount = 0;
    outlierLastCandidate = val;
    return true;
  }

  window.addEventListener("message", (event) => {
    if (!event.data || event.data.source !== "SAFIRION_WS_INTERCEPT") return;
    try {
      const data = JSON.parse(event.data.payload);
      extractCleanQuoteAndBalance(data);
    } catch (e) {}
  });

  function extractCleanQuoteAndBalance(obj) {
    if (!obj || typeof obj !== "object") return;

    if (typeof obj.balance === "number" && obj.balance > 0 && obj.balance < 10000000) {
      lastDetectedBalance = obj.balance > 50000 && Number.isInteger(obj.balance) ? obj.balance / 100 : obj.balance;
    } else if (typeof obj.user_balance === "number" && obj.user_balance > 0) {
      lastDetectedBalance = obj.user_balance;
    }

    // Se o canal atual ficou silencioso por > 8s (ex: nova aba aberta por "+ NOVA OPÇÃO"), libera o activeAssetId!
    if (Date.now() - lastTickSentAtMs > 8000) {
      activeAssetId = null;
    }

    if (obj.active_id && activeAssetId !== null && obj.active_id !== activeAssetId) {
      return;
    }

    // Só aceita preços que venham de pacotes reais de cotação/vela (quote, candle OHLC ou active_id + value/close),
    // ignorando completamente objetos de configuração/conversão de fundo!
    let candidate = null;
    if (typeof obj.quote === "number") {
      candidate = obj.quote;
    } else if (
      typeof obj.close === "number" &&
      (typeof obj.open === "number" || typeof obj.max === "number" || typeof obj.high === "number")
    ) {
      candidate = obj.close;
    } else if (
      (obj.active_id !== undefined || obj.asset_id !== undefined || obj.instrument_id !== undefined) &&
      typeof (obj.value ?? obj.close ?? obj.bid ?? obj.price) === "number"
    ) {
      candidate = obj.value ?? obj.close ?? obj.bid ?? obj.price;
    }

    if (typeof candidate === "number" && !Number.isInteger(candidate)) {
      if (isStrictSameAssetTick(candidate)) {
        if (obj.active_id && activeAssetId === null) {
          activeAssetId = obj.active_id;
        }
        lastValidPrice = candidate;
        sendTickToBot(candidate);
        return;
      }
    }

    if (obj.msg && typeof obj.msg === "object") {
      extractCleanQuoteAndBalance(obj.msg);
    } else if (Array.isArray(obj)) {
      for (const item of obj) extractCleanQuoteAndBalance(item);
    }
  }

  function sendTickToBot(price) {
    lastTickSentAtMs = Date.now();
    const hudPrice = document.getElementById("rt-hud-price");
    if (hudPrice) hudPrice.textContent = price.toFixed(5);

    // Destrava "+ NOVA OPÇÃO" automaticamente assim que o tempo da operação encerra (sem throttling em janela minimizada!)
    if (needsNovaOpcaoUnlock && tradeAutoUnlockAtMs > 0 && Date.now() >= tradeAutoUnlockAtMs) {
      unlockNovaOpcaoNow();
    }

    if (bridgeSocket && bridgeSocket.readyState === WebSocket.OPEN) {
      const { domBalance, lastResultPnl } = readSafirionDomState();
      bridgeSocket.send(
        JSON.stringify({
          type: "SAFIRION_TICK",
          price: price,
          balance: domBalance,
          last_result_pnl: lastResultPnl,
          call_bound: Boolean(boundTargets.CALL),
          put_bound: Boolean(boundTargets.PUT),
          symbol: detectedSymbol,
          sub_type: detectedMode,
          timestamp: Date.now(),
        })
      );
    }
  }

  // Rotina contínua de Auto-Sincronização (1.5s): Heartbeat, Watchdog de Conexão, Destravamento e Limpeza de Abas
  setInterval(() => {
    const now = Date.now();

    if (needsNovaOpcaoUnlock && tradeAutoUnlockAtMs > 0 && now >= tradeAutoUnlockAtMs) {
      unlockNovaOpcaoNow();
    }

    // Se ficou mais de 8s sem receber tick válido, libera o filtro de ativo para ressincronizar na hora
    if (now - lastTickSentAtMs > 8000) {
      activeAssetId = null;
      outlierStreakCount = 0;
    }

    // Watchdog da Ponte WebSocket: se desconectou ou ficou > 15s sem resposta PONG do servidor, reconecta sozinho!
    if (!bridgeSocket || bridgeSocket.readyState === WebSocket.CLOSED) {
      connectToBotServer();
    } else if (bridgeSocket.readyState === WebSocket.OPEN) {
      if (now - lastServerPongAtMs > 16000) {
        try {
          bridgeSocket.close();
        } catch (e) {}
        connectToBotServer();
      } else {
        const { domBalance, lastResultPnl } = readSafirionDomState();
        try {
          bridgeSocket.send(
            JSON.stringify({
              type: "BRIDGE_HEARTBEAT",
              balance: domBalance,
              last_result_pnl: lastResultPnl,
              price: lastValidPrice,
              call_bound: Boolean(boundTargets.CALL),
              put_bound: Boolean(boundTargets.PUT),
              sub_type: detectedMode,
            })
          );
        } catch (e) {}
      }
    }

    // Verifica e fecha automaticamente abas duplicadas no topo da Safirion (mantendo apenas a aba ativa mais recente)
    closeDuplicateAssetTabs();
  }, 1500);

  /**
   * Localiza todas as abas de ativos abertas no cabeçalho superior da Safirion (ex: "EUR/USD (O... +R$ 4.45")
   * e retorna a lista ordenada da esquerda (mais antiga) para a direita (mais recente/ativa).
   */
  function findTopBarAssetTabs() {
    try {
      const hud = document.getElementById("rt-safirion-hud");
      const all = document.querySelectorAll("div, button, li, a, section");
      const rawCandidates = [];

      for (const el of all) {
        if (hud && hud.contains(el)) continue;
        const r = el.getBoundingClientRect();
        // As abas de ativos ficam na barra superior: Y entre 2px e 72px, X a partir de 115px até 75% da largura
        if (
          r.top >= 2 &&
          r.top <= 72 &&
          r.left >= 115 &&
          r.left <= window.innerWidth * 0.75 &&
          r.width >= 62 &&
          r.width <= 175 &&
          r.height >= 26 &&
          r.height <= 58
        ) {
          const txt = (el.textContent || "").replace(/\s+/g, " ").trim();
          if (!txt || txt.length < 3 || txt.length > 65) continue;

          // Verifica se o texto parece um ativo da Safirion (ex: EUR/USD, OTC, R$, BTC, GBP, etc.)
          const isAssetTab =
            txt.includes("/") ||
            /OTC|EUR|USD|GBP|JPY|AUD|CAD|CHF|NZD|BTC|ETH|XAU|IDX|R\$/i.test(txt);

          if (isAssetTab) {
            rawCandidates.push({
              el,
              rect: r,
              left: r.left,
              area: r.width * r.height,
              text: txt,
            });
          }
        }
      }

      // Agrupa elementos filhos/pais que pertencem à mesma aba física (mesma posição horizontal X)
      rawCandidates.sort((a, b) => a.left - b.left);
      const tabClusters = [];

      for (const cand of rawCandidates) {
        const existing = tabClusters.find((c) => Math.abs(c.left - cand.left) < 38);
        if (!existing) {
          tabClusters.push(cand);
        } else if (cand.area > existing.area && cand.rect.width <= 150 && cand.rect.height <= 54) {
          // Prefere o container externo completo da aba para achar o botão "x" no canto superior esquerdo
          const idx = tabClusters.indexOf(existing);
          tabClusters[idx] = cand;
        }
      }

      return tabClusters.sort((a, b) => a.left - b.left);
    } catch (e) {
      return [];
    }
  }

  let isClosingTabs = false;

  /**
   * Fecha automaticamente as abas duplicadas/antigas à esquerda na barra superior da Safirion,
   * preservando sempre a última aba da direita (que é a aba recém-aberta / ativa).
   */
  async function closeDuplicateAssetTabs() {
    if (isClosingTabs || mappingTarget) return 0;
    const tabs = findTopBarAssetTabs();
    if (tabs.length <= 1) return 0;

    isClosingTabs = true;
    let closedCount = 0;

    try {
      // Fecha uma por uma da esquerda para a direita (até sobrar apenas 1 aba ativa)
      for (let attempt = 0; attempt < 6; attempt++) {
        const currentTabs = findTopBarAssetTabs();
        if (currentTabs.length <= 1) break;

        // A aba mais à esquerda (índice 0) é sempre a aba antiga/finalizada que ficou para trás
        const oldTab = currentTabs[0];
        const r = oldTab.rect;

        // Na Safirion, o ícone "x" de fechar a aba fica no canto superior esquerdo da aba (left + 8, top + 8)
        const closeX = Math.round(r.left + 8);
        const closeY = Math.round(r.top + 8);

        // Procura também o elemento DOM exato do botão "x" dentro da aba (svg, button, span, path)
        let closeEl = null;
        const innerNodes = oldTab.el.querySelectorAll("button, svg, path, span, i, div");
        for (const node of innerNodes) {
          const nr = node.getBoundingClientRect();
          const cls = ((node.className && String(node.className)) || "").toLowerCase();
          const txt = (node.textContent || "").trim().toLowerCase();
          if (
            nr.width > 0 &&
            nr.width <= 26 &&
            nr.height > 0 &&
            nr.height <= 26 &&
            nr.left <= r.left + 28 &&
            nr.top <= r.top + 24
          ) {
            closeEl = node;
            if (cls.includes("close") || txt === "x" || txt === "×" || txt === "✕" || node.tagName.toLowerCase() === "svg") {
              break;
            }
          }
        }

        const closeTarget = {
          el: closeEl || oldTab.el,
          rawEl: closeEl || oldTab.el,
          clientX: closeX,
          clientY: closeY,
          screenX: closeX,
          screenY: closeY,
        };

        triggerCoordAndDomClick(closeTarget, "CLOSE_TAB");
        closedCount++;

        await new Promise((res) => setTimeout(res, 240));
      }

      if (closedCount > 0) {
        updateHudStatus(`🧹 ${closedCount} aba(s) duplicada(s) fechada(s)!`, "#38bdf8");
      }
    } catch (e) {}

    isClosingTabs = false;
    return closedCount;
  }

  // Calcula automaticamente as coordenadas do campo "Invest" e de "+ NOVA OPÇÃO" a partir de CALL e PUT
  function autoDeriveAuxTargets() {
    if (boundTargets.CALL && boundTargets.PUT) {
      const callX = boundTargets.CALL.clientX;
      const callY = boundTargets.CALL.clientY;
      const putX = boundTargets.PUT.clientX;
      const putY = boundTargets.PUT.clientY;

      // 1. O botão azul "+ NOVA OPÇÃO" fica exatamente no ponto médio entre CALL (352) e PUT (451) -> (1465, 402)
      if (!boundTargets.NOVA_OPCAO || boundTargets.NOVA_OPCAO.auto) {
        boundTargets.NOVA_OPCAO = {
          el: boundTargets.CALL.el,
          rawEl: boundTargets.CALL.rawEl,
          clientX: Math.round((callX + putX) / 2),
          clientY: Math.round((callY + putY) / 2),
          screenX: Math.round((boundTargets.CALL.screenX + boundTargets.PUT.screenX) / 2),
          screenY: Math.round((boundTargets.CALL.screenY + boundTargets.PUT.screenY) / 2),
          auto: true,
        };
      }

      // 2. Campo de entrada "Invest (R$)" (centro do campo numérico acima do botão CALL: X = callX - 10, Y = 138)
      if (!boundTargets.INVEST || boundTargets.INVEST.auto) {
        boundTargets.INVEST = {
          el: null,
          rawEl: null,
          clientX: Math.max(10, callX - 10),
          clientY: 138,
          screenX: Math.max(10, boundTargets.CALL.screenX - 10),
          screenY: 138,
          auto: true,
        };
      }
    }
  }

  // Localiza botões ou o campo Invest no DOM por palavra-chave ou posição geométrica (funciona mesmo com tela minimizada)
  function findDomButtonByKeyword(keyword) {
    try {
      const hud = document.getElementById("rt-safirion-hud");

      // Busca específica para a entrada de valor "Invest (R$)" na barra lateral direita
      if (keyword === "INVEST") {
        const inputs = Array.from(document.querySelectorAll("input, textarea, [contenteditable='true']")).filter(
          (inp) =>
            (!hud || !hud.contains(inp)) &&
            inp.type !== "checkbox" &&
            inp.type !== "radio" &&
            inp.type !== "hidden"
        );
        const rightInputs = inputs.filter((inp) => {
          const r = inp.getBoundingClientRect();
          const val = (inp.value || inp.textContent || "").trim();
          const isTimeField = /\d{1,2}:\d{2}/.test(val);
          return r.left > window.innerWidth * 0.6 && r.top >= 0 && r.top < 300 && r.width > 5 && !isTimeField;
        });
        rightInputs.sort((a, b) => {
          const aHasRs = /R\$|Invest/i.test((a.value || "") + " " + (a.parentElement?.textContent || ""));
          const bHasRs = /R\$|Invest/i.test((b.value || "") + " " + (b.parentElement?.textContent || ""));
          if (aHasRs && !bHasRs) return -1;
          if (!aHasRs && bHasRs) return 1;
          return a.getBoundingClientRect().top - b.getBoundingClientRect().top;
        });
        if (rightInputs.length > 0) return rightInputs[0];

        const els = document.querySelectorAll("div, span, label");
        for (const el of els) {
          if (hud && hud.contains(el)) continue;
          const r = el.getBoundingClientRect();
          if (r.left > window.innerWidth * 0.72 && r.top >= 15 && r.top <= 220 && r.width >= 20 && r.width <= 180 && r.height >= 14 && r.height <= 75) {
            const txt = (el.textContent || "").replace(/\s+/g, " ").trim();
            if (/^R\$\s*\d+/i.test(txt)) {
              return el;
            }
          }
        }
        return null;
      }

      const all = document.querySelectorAll("button, [role='button'], a, div, span");
      const matches = [];

      for (const el of all) {
        if (hud && hud.contains(el)) continue;
        const key = cleanAlpha(el.textContent);
        if (!key) continue;

        let matched = false;
        if (keyword === "NOVAOPCAO") {
          matched =
            key === "NOVAOPCAO" ||
            key === "NEWOPTION" ||
            (key.includes("NOVAOPCAO") && key.length <= 20) ||
            ((key === "OPCAO" || key === "NOVA") &&
              el.parentElement &&
              cleanAlpha(el.parentElement.textContent).includes("NOVAOPCAO"));
        } else if (keyword === "CALL") {
          matched = key === "ACIMA" || key === "HIGHER" || key === "CALL";
        } else if (keyword === "PUT") {
          matched = key === "ABAIXO" || key === "LOWER" || key === "PUT";
        }

        if (matched) {
          const r = el.getBoundingClientRect();
          if (r.width > 0 && r.width <= 340 && r.height > 0 && r.height <= 280) {
            matches.push({ el, rect: r, area: r.width * r.height });
          }
        }
      }

      matches.sort((a, b) => a.area - b.area);
      return matches.length > 0 ? matches[0].el : null;
    } catch (e) {
      return null;
    }
  }

  // Executa clique completo (Coordenada + Canvas + DOM + React Fiber no mundo MAIN)
  function triggerCoordAndDomClick(targetObj, keywordHint) {
    if (!targetObj && !keywordHint) return false;

    const hud = document.getElementById("rt-safirion-hud");
    const cx = targetObj ? targetObj.clientX : 1465;
    const cy = targetObj ? targetObj.clientY : 402;
    const sx = targetObj ? targetObj.screenX || cx : cx;
    const sy = targetObj ? targetObj.screenY || cy : cy;

    const domByKeyword = keywordHint ? findDomButtonByKeyword(keywordHint) : null;
    const ptEl = document.elementFromPoint(cx, cy);
    const validPtEl = ptEl && (!hud || !hud.contains(ptEl)) ? ptEl : null;
    const savedEl = targetObj && targetObj.el && document.body.contains(targetObj.el) ? targetObj.el : null;
    const canvasEl = document.querySelector("canvas");

    const primaryEl = domByKeyword || validPtEl || savedEl || canvasEl;
    if (!primaryEl) return false;

    const chain = [];
    const seen = new Set();
    const candidates = [domByKeyword, validPtEl, savedEl, canvasEl].filter(Boolean);

    for (const startNode of candidates) {
      let curr = startNode;
      for (let i = 0; i < 4 && curr && curr !== document.body && curr !== document.documentElement; i++) {
        if (!seen.has(curr) && (!hud || !hud.contains(curr))) {
          seen.add(curr);
          chain.push(curr);
        }
        curr = curr.parentElement;
      }
    }

    const eventInit = {
      bubbles: true,
      cancelable: true,
      composed: true,
      view: window,
      detail: 1,
      screenX: sx,
      screenY: sy,
      clientX: cx,
      clientY: cy,
      button: 0,
      buttons: 1,
      pointerId: 1,
      pointerType: "mouse",
      isPrimary: true,
    };

    for (const node of chain) {
      try {
        node.dispatchEvent(new PointerEvent("pointerover", eventInit));
        node.dispatchEvent(new MouseEvent("mouseover", eventInit));
        node.dispatchEvent(new PointerEvent("pointerdown", eventInit));
        node.dispatchEvent(new MouseEvent("mousedown", eventInit));
        node.dispatchEvent(new PointerEvent("pointerup", { ...eventInit, buttons: 0 }));
        node.dispatchEvent(new MouseEvent("mouseup", { ...eventInit, buttons: 0 }));
        node.dispatchEvent(new MouseEvent("click", { ...eventInit, buttons: 0 }));
        if (typeof node.click === "function") node.click();
        fireReactClick(node, cx, cy, false);
      } catch (e) {}
    }

    return true;
  }

  // Destrava o botão "+ NOVA OPÇÃO" e em seguida fecha a aba antiga duplicada que ficou para trás!
  function unlockNovaOpcaoNow() {
    autoDeriveAuxTargets();
    const domNova = findDomButtonByKeyword("NOVAOPCAO");

    if (!needsNovaOpcaoUnlock && !domNova) {
      return false;
    }

    needsNovaOpcaoUnlock = false;
    tradeAutoUnlockAtMs = 0;

    if (boundTargets.NOVA_OPCAO) {
      triggerCoordAndDomClick(boundTargets.NOVA_OPCAO, "NOVAOPCAO");
    } else if (domNova) {
      const r = domNova.getBoundingClientRect();
      triggerCoordAndDomClick(
        {
          el: domNova,
          clientX: Math.round(r.left + r.width / 2),
          clientY: Math.round(r.top + r.height / 2),
        },
        "NOVAOPCAO"
      );
    }

    updateHudStatus("🔄 'Nova Opção' acionada + limpando aba duplicada...", "#38bdf8");

    // Aguarda a Safirion abrir a nova aba (450ms) e já fecha a aba anterior duplicada!
    setTimeout(() => {
      closeDuplicateAssetTabs();
    }, 450);
    setTimeout(() => {
      closeDuplicateAssetTabs();
    }, 1100);

    return true;
  }

  /**
   * Altera o valor no campo "Invest (R$)" da Safirion dando um DOUBLE CLICK na entrada Invest
   * (o que seleciona todo o número atual) e digitando o novo valor diretamente (sem usar Backspace nem botões +/-)!
   */
  async function setSafirionInvestmentAmount(targetAmount) {
    const desired = Math.max(5, Math.round(Number(targetAmount) || 5));
    const valStr = String(desired);
    autoDeriveAuxTargets();

    // Lê o valor atual visível no DOM da Safirion (ou usa o valor rastreado)
    let current = readCurrentInvestAmountFromDom() ?? trackedInvestAmount;
    if (Math.abs(current - desired) < 0.5) {
      return;
    }

    const hud = document.getElementById("rt-safirion-hud");
    let domInvest = findDomButtonByKeyword("INVEST");

    let cx = boundTargets.INVEST ? boundTargets.INVEST.clientX : Math.round(window.innerWidth - 75);
    let cy = boundTargets.INVEST ? boundTargets.INVEST.clientY : 138;

    // Se o usuário não vinculou manualmente o campo Invest, prioriza a coordenada real do elemento Invest no DOM
    if (domInvest && (!boundTargets.INVEST || boundTargets.INVEST.auto)) {
      const r = domInvest.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        cx = Math.round(r.left + r.width / 2);
        cy = Math.round(r.top + r.height / 2);
      }
    }

    const ptEl = document.elementFromPoint(cx, cy);
    const clickEl =
      (boundTargets.INVEST && !boundTargets.INVEST.auto && boundTargets.INVEST.el && document.body.contains(boundTargets.INVEST.el)
        ? boundTargets.INVEST.el
        : null) ||
      domInvest ||
      (ptEl && (!hud || !hud.contains(ptEl)) ? ptEl : null);

    // PASSO 1: Dispara o DOUBLE CLICK (Clique 1 + Clique 2 + dblclick) na entrada Invest!
    if (clickEl) {
      const mkPtr = (type, detail, buttons) =>
        new PointerEvent(type, {
          bubbles: true,
          cancelable: true,
          composed: true,
          view: window,
          detail: detail,
          clientX: cx,
          clientY: cy,
          screenX: cx,
          screenY: cy,
          button: 0,
          buttons: buttons,
          pointerId: 1,
          pointerType: "mouse",
          isPrimary: true,
        });
      const mkMouse = (type, detail, buttons) =>
        new MouseEvent(type, {
          bubbles: true,
          cancelable: true,
          composed: true,
          view: window,
          detail: detail,
          clientX: cx,
          clientY: cy,
          screenX: cx,
          screenY: cy,
          button: 0,
          buttons: buttons,
        });

      let curr = clickEl;
      for (let i = 0; i < 4 && curr && curr !== document.body; i++) {
        if (!hud || !hud.contains(curr)) {
          try {
            curr.dispatchEvent(mkPtr("pointerdown", 1, 1));
            curr.dispatchEvent(mkMouse("mousedown", 1, 1));
            curr.dispatchEvent(mkPtr("pointerup", 1, 0));
            curr.dispatchEvent(mkMouse("mouseup", 1, 0));
            curr.dispatchEvent(mkMouse("click", 1, 0));

            curr.dispatchEvent(mkPtr("pointerdown", 2, 1));
            curr.dispatchEvent(mkMouse("mousedown", 2, 1));
            curr.dispatchEvent(mkPtr("pointerup", 2, 0));
            curr.dispatchEvent(mkMouse("mouseup", 2, 0));
            curr.dispatchEvent(mkMouse("click", 2, 0));
            curr.dispatchEvent(mkMouse("dblclick", 2, 0));
            fireReactClick(curr, cx, cy, true);
          } catch (e) {}
        }
        curr = curr.parentElement;
      }
    }

    // Aguarda 25ms para o double-click focar/selecionar o input na Safirion
    await new Promise((r) => setTimeout(r, 25));

    // Re-localiza o elemento focado ou <input> aberto pelo double-click
    domInvest = findDomButtonByKeyword("INVEST") || domInvest;
    const investInput =
      domInvest && domInvest.tagName === "INPUT"
        ? domInvest
        : document.activeElement &&
          document.activeElement.tagName === "INPUT" &&
          (!hud || !hud.contains(document.activeElement))
        ? document.activeElement
        : clickEl && clickEl.tagName === "INPUT"
        ? clickEl
        : clickEl
        ? clickEl.querySelector?.("input") || clickEl.closest?.("div, label")?.querySelector?.("input")
        : null;

    const activeNode = investInput || document.activeElement || clickEl || document.body;

    // PASSO 2: Com o campo selecionado pelo double-click, digita o novo valor diretamente por cima da seleção!
    if (investInput) {
      const initialRawValue = investInput.value || "";
      const hadRsPrefix = /R\$/i.test(initialRawValue);

      investInput.focus();
      if (typeof investInput.select === "function") {
        investInput.select();
      }
      try {
        investInput.setSelectionRange(0, initialRawValue.length);
      } catch (e) {}

      // Digita dígito por dígito atualizando o _valueTracker do React ANTES de mudar o .value!
      for (let i = 0; i < valStr.length; i++) {
        const ch = valStr[i];
        const kc = ch.charCodeAt(0);
        const partialDigits = valStr.slice(0, i + 1);

        const keyPayload = {
          key: ch,
          code: `Digit${ch}`,
          keyCode: kc,
          which: kc,
          charCode: kc,
          bubbles: true,
          cancelable: true,
          isTrusted: true,
          preventDefault: function () {},
          stopPropagation: function () {},
          persist: function () {},
        };

        investInput.dispatchEvent(new KeyboardEvent("keydown", keyPayload));
        investInput.dispatchEvent(new KeyboardEvent("keypress", keyPayload));
        invokeReactHandlersOnChain(investInput, ["onKeyDown", "onKeyPress"], keyPayload);

        forceReactInputValue(investInput, partialDigits, ch);

        investInput.dispatchEvent(new KeyboardEvent("keyup", keyPayload));
        invokeReactHandlersOnChain(investInput, ["onKeyUp"], keyPayload);
      }

      // Se a Safirion exigir o prefixo "R$ " no value, aplica com o prefixo
      const afterDigits = (investInput.value || "").replace(/[^\d]/g, "");
      if (afterDigits !== valStr) {
        forceReactInputValue(investInput, hadRsPrefix ? `R$ ${valStr}` : valStr, valStr);
      }

      // PASSO 3: Confirma com Enter e Blur para fechar a seleção e gravar o novo valor
      const enterPayload = {
        key: "Enter",
        code: "Enter",
        keyCode: 13,
        which: 13,
        charCode: 13,
        bubbles: true,
        cancelable: true,
        isTrusted: true,
        preventDefault: function () {},
        stopPropagation: function () {},
        persist: function () {},
      };
      investInput.dispatchEvent(new KeyboardEvent("keydown", enterPayload));
      investInput.dispatchEvent(new KeyboardEvent("keypress", enterPayload));
      investInput.dispatchEvent(new KeyboardEvent("keyup", enterPayload));
      invokeReactHandlersOnChain(investInput, ["onKeyDown", "onKeyPress", "onKeyUp"], enterPayload);

      investInput.dispatchEvent(new Event("change", { bubbles: true }));
      investInput.dispatchEvent(new FocusEvent("blur", { bubbles: false }));
      investInput.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
      invokeReactHandlersOnChain(investInput, ["onBlur", "onFocusOut"], {
        bubbles: true,
        target: investInput,
        currentTarget: investInput,
        preventDefault: function () {},
        stopPropagation: function () {},
        persist: function () {},
      });
      if (typeof investInput.blur === "function") {
        investInput.blur();
      }
    } else {
      // Fallback caso o campo Invest seja um elemento customizado (div/span) selecionado pelo double-click
      for (let i = 0; i < valStr.length; i++) {
        const ch = valStr[i];
        const kc = ch.charCodeAt(0);
        const partialDigits = valStr.slice(0, i + 1);
        const keyInit = {
          key: ch,
          code: `Digit${ch}`,
          keyCode: kc,
          which: kc,
          charCode: kc,
          bubbles: true,
          cancelable: true,
          composed: true,
          view: window,
        };
        const reactKeyPayload = {
          ...keyInit,
          isTrusted: true,
          preventDefault: function () {},
          stopPropagation: function () {},
          persist: function () {},
        };

        for (const tgt of [activeNode, document.activeElement, document, window].filter(Boolean)) {
          try {
            tgt.dispatchEvent(new KeyboardEvent("keydown", keyInit));
            tgt.dispatchEvent(new KeyboardEvent("keypress", keyInit));
          } catch (e) {}
        }
        invokeReactHandlersOnChain(activeNode, ["onKeyDown", "onKeyPress"], reactKeyPayload);

        try {
          document.execCommand("insertText", false, ch);
        } catch (e) {}
        try {
          activeNode.dispatchEvent(
            new InputEvent("beforeinput", { data: ch, inputType: "insertText", bubbles: true, cancelable: true })
          );
          activeNode.dispatchEvent(
            new InputEvent("input", { data: ch, inputType: "insertText", bubbles: true })
          );
        } catch (e) {}
        invokeReactHandlersOnChain(activeNode, ["onInput", "onChange"], {
          target: { value: partialDigits },
          currentTarget: { value: partialDigits },
          preventDefault: function () {},
          stopPropagation: function () {},
          persist: function () {},
        });

        for (const tgt of [activeNode, document.activeElement, document, window].filter(Boolean)) {
          try {
            tgt.dispatchEvent(new KeyboardEvent("keyup", keyInit));
          } catch (e) {}
        }
        invokeReactHandlersOnChain(activeNode, ["onKeyUp"], reactKeyPayload);
      }

      const enterInit = {
        key: "Enter",
        code: "Enter",
        keyCode: 13,
        which: 13,
        charCode: 13,
        bubbles: true,
        cancelable: true,
        view: window,
      };
      for (const tgt of [activeNode, document.activeElement, document, window].filter(Boolean)) {
        try {
          tgt.dispatchEvent(new KeyboardEvent("keydown", enterInit));
          tgt.dispatchEvent(new KeyboardEvent("keyup", enterInit));
        } catch (e) {}
      }
      invokeReactHandlersOnChain(activeNode, ["onKeyDown", "onKeyUp", "onBlur"], {
        ...enterInit,
        isTrusted: true,
        preventDefault: function () {},
        stopPropagation: function () {},
        persist: function () {},
      });
    }

    await new Promise((r) => setTimeout(r, 25));

    const updatedDomVal = readCurrentInvestAmountFromDom();
    trackedInvestAmount = updatedDomVal !== null ? updatedDomVal : desired;
    updateHudStatus(`💰 Invest: R$ ${trackedInvestAmount}`, "#fbbf24");
  }

  async function executeOrderInBackground(direction, amount, subType, expirationMinutes) {
    autoDeriveAuxTargets();
    const targetObj = boundTargets[direction];
    if (!targetObj) {
      updateHudStatus(`⚠️ Vincule o botão ${direction} na barra acima!`, "#f43f5e");
      return {
        success: false,
        message: `Botão ${direction} ainda não vinculado na barra azul no topo da Safirion!`,
      };
    }

    // PASSO 1: Se a operação anterior deixou o botão "+ NOVA OPÇÃO" na tela, clica nele primeiro e aguarda 350ms!
    if (needsNovaOpcaoUnlock || findDomButtonByKeyword("NOVAOPCAO")) {
      unlockNovaOpcaoNow();
      await new Promise((r) => setTimeout(r, 350));
    }

    // PASSO 2: Ajusta o valor no campo "Invest (R$)" via Double-Click + Digitação Direta!
    const targetAmount = Math.max(5, Math.round(Number(amount) || 5));
    await setSafirionInvestmentAmount(targetAmount);

    // PASSO 3: Clica em ACIMA (CALL) ou ABAIXO (PUT)
    const ok = triggerCoordAndDomClick(targetObj, direction);
    if (ok) {
      needsNovaOpcaoUnlock = true;
      const expMin = Math.max(1, Number(expirationMinutes) || 1);
      tradeAutoUnlockAtMs = Date.now() + expMin * 60 * 1000 + 2000;

      updateHudStatus(`⚡ ${direction} (R$ ${trackedInvestAmount}) enviado!`, "#10b981");
      return {
        success: true,
        message: `Ordem ${direction} de R$ ${trackedInvestAmount} (${subType}) executada na Safirion @ ${
          lastValidPrice ? lastValidPrice.toFixed(5) : ""
        }!`,
      };
    }

    updateHudStatus(`⚠️ Falha ao clicar em ${direction}!`, "#f43f5e");
    return {
      success: false,
      message: `Não foi possível clicar em ${direction} na Safirion.`,
    };
  }

  function createFloatingHud() {
    const hud = document.createElement("div");
    hud.id = "rt-safirion-hud";

    // Restaura posição e estado salvo pelo usuário
    let savedState = {};
    try {
      savedState = JSON.parse(localStorage.getItem("rt_hud_ui_state") || "{}");
    } catch (e) {}

    let isMinimized = Boolean(savedState.minimized);
    let showAdvanced = false;
    let hudScale = Number(savedState.scale) || 1.0;

    hud.style.cssText = `
      position: fixed;
      top: ${savedState.top || "8px"};
      left: ${savedState.left || "50%"};
      transform: ${savedState.left ? "none" : "translateX(-50%)"};
      z-index: 2147483647;
      background: rgba(9, 13, 22, 0.92);
      backdrop-filter: blur(8px);
      border: 1px solid rgba(6, 182, 212, 0.65);
      border-radius: 9px;
      padding: 4px 8px;
      color: #f8fafc;
      font-family: system-ui, -apple-system, sans-serif;
      font-size: 11px;
      display: flex;
      align-items: center;
      gap: 5px;
      box-shadow: 0 6px 20px rgba(0,0,0,0.65);
      user-select: none;
      resize: horizontal;
      overflow: hidden;
      transition: padding 0.15s ease, border-radius 0.15s ease;
    `;

    hud.innerHTML = `
      <!-- Alça para arrastar o painel para qualquer lugar -->
      <span id="rt-hud-drag" title="Arraste para mover a barra" style="cursor:grab;color:#64748b;font-weight:bold;padding:0 3px;font-size:13px;">⠿</span>

      <!-- Visão Minimizável (sempre visível) -->
      <span id="rt-hud-brand" style="font-weight:800;color:#22d3ee;white-space:nowrap;cursor:pointer;" title="Clique para expandir/minimizar">🤖 RT</span>
      <span id="rt-hud-price" style="color:#38bdf8;font-weight:700;font-family:monospace;font-size:11px;white-space:nowrap;">--</span>

      <!-- Corpo Principal (ocultado quando minimizado) -->
      <div id="rt-hud-body" style="display:flex;align-items:center;gap:5px;flex-wrap:wrap;">
        <select id="rt-mode-select" style="background:#1e293b;color:#fff;border:1px solid #334155;border-radius:5px;padding:1px 4px;font-size:10px;">
          <option value="BINARY">Binárias</option>
          <option value="DIGITAL">Digital</option>
          <option value="BLITZ">Blitz</option>
        </select>

        <button id="rt-bind-call" title="Vincular botão VERDE (CALL)" style="background:#059669;color:#fff;border:none;border-radius:5px;padding:2px 6px;cursor:pointer;font-weight:bold;font-size:10px;white-space:nowrap;">
          CALL ▲
        </button>
        <button id="rt-bind-put" title="Vincular botão VERMELHO (PUT)" style="background:#e11d48;color:#fff;border:none;border-radius:5px;padding:2px 6px;cursor:pointer;font-weight:bold;font-size:10px;white-space:nowrap;">
          PUT ▼
        </button>

        <!-- Botão para mostrar/esconder os botões extras (Invest, Nova Opção, Sync) -->
        <button id="rt-toggle-adv" title="Mostrar/Ocultar ajustes extras (Campo Invest, Nova Opção, Sync)" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:5px;padding:2px 5px;cursor:pointer;font-size:10px;">
          ⚙️
        </button>

        <div id="rt-hud-adv" style="display:none;align-items:center;gap:4px;">
          <button id="rt-bind-invest" title="Vincular campo de texto 'Invest (R$)' (usado para Double-Click + Digitação)" style="background:#d97706;color:#fff;border:none;border-radius:4px;padding:2px 5px;cursor:pointer;font-weight:bold;font-size:10px;">
            Invest Auto
          </button>
          <button id="rt-bind-nova" title="Vincular botão '+ NOVA OPÇÃO'" style="background:#2563eb;color:#fff;border:none;border-radius:4px;padding:2px 5px;cursor:pointer;font-weight:bold;font-size:10px;">
            Nova Opção
          </button>
          <button id="rt-reset-price" title="Ressincronizar ativo" style="background:#1e293b;color:#cbd5e1;border:1px solid #475569;border-radius:4px;padding:2px 5px;cursor:pointer;font-size:10px;">
            Sync
          </button>
        </div>

        <span id="rt-hud-status" style="color:#94a3b8;font-size:10px;max-width:210px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">Conectando...</span>
      </div>

      <!-- Controles de Tamanho (Zoom -/+) e Botão Minimizar/Restaurar -->
      <div style="display:flex;align-items:center;gap:2px;margin-left:2px;">
        <button id="rt-hud-smaller" title="Diminuir tamanho da barra" style="background:transparent;color:#94a3b8;border:none;cursor:pointer;font-size:10px;padding:1px 3px;">-</button>
        <button id="rt-hud-larger" title="Aumentar tamanho da barra" style="background:transparent;color:#94a3b8;border:none;cursor:pointer;font-size:10px;padding:1px 3px;">+</button>
        <button id="rt-hud-minimize" title="Minimizar / Expandir extensão" style="background:#1e293b;color:#38bdf8;border:1px solid #334155;border-radius:4px;cursor:pointer;font-weight:bold;font-size:10px;padding:1px 6px;line-height:1.2;">
          —
        </button>
      </div>
    `;

    document.body.appendChild(hud);

    const bodyEl = document.getElementById("rt-hud-body");
    const advEl = document.getElementById("rt-hud-adv");
    const minBtn = document.getElementById("rt-hud-minimize");
    const dragHandle = document.getElementById("rt-hud-drag");

    function saveUiState() {
      try {
        localStorage.setItem(
          "rt_hud_ui_state",
          JSON.stringify({
            minimized: isMinimized,
            top: hud.style.top,
            left: hud.style.left,
            scale: hudScale,
          })
        );
      } catch (e) {}
    }

    function applyMinimizeState() {
      if (isMinimized) {
        bodyEl.style.display = "none";
        minBtn.textContent = "▢";
        minBtn.title = "Expandir barra do RoboTrader";
        hud.style.padding = "3px 7px";
        hud.style.borderRadius = "999px";
        hud.style.resize = "none";
      } else {
        bodyEl.style.display = "flex";
        minBtn.textContent = "—";
        minBtn.title = "Minimizar para pílula compacta";
        hud.style.padding = "4px 8px";
        hud.style.borderRadius = "9px";
        hud.style.resize = "horizontal";
      }
      hud.style.zoom = String(hudScale);
      saveUiState();
    }

    applyMinimizeState();

    // Minimizar / Expandir pelo botão ou clicando na logo 🤖 RT
    minBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      isMinimized = !isMinimized;
      applyMinimizeState();
    });
    document.getElementById("rt-hud-brand").addEventListener("click", (e) => {
      e.stopPropagation();
      isMinimized = !isMinimized;
      applyMinimizeState();
    });

    // Redimensionar (Escala - / +)
    document.getElementById("rt-hud-smaller").addEventListener("click", (e) => {
      e.stopPropagation();
      hudScale = Math.max(0.75, Math.round((hudScale - 0.08) * 100) / 100);
      hud.style.zoom = String(hudScale);
      saveUiState();
    });
    document.getElementById("rt-hud-larger").addEventListener("click", (e) => {
      e.stopPropagation();
      hudScale = Math.min(1.35, Math.round((hudScale + 0.08) * 100) / 100);
      hud.style.zoom = String(hudScale);
      saveUiState();
    });

    // Mostrar/Ocultar botões extras (Invest, Nova Opção, Sync)
    document.getElementById("rt-toggle-adv").addEventListener("click", (e) => {
      e.stopPropagation();
      showAdvanced = !showAdvanced;
      advEl.style.display = showAdvanced ? "flex" : "none";
    });

    // Sistema de Arrastar (Drag & Drop) pela alça ⠿
    let isDragging = false;
    let dragOffsetX = 0;
    let dragOffsetY = 0;

    dragHandle.addEventListener("mousedown", (e) => {
      e.stopPropagation();
      isDragging = true;
      dragHandle.style.cursor = "grabbing";
      const rect = hud.getBoundingClientRect();
      hud.style.transform = "none";
      hud.style.left = rect.left + "px";
      hud.style.top = rect.top + "px";
      dragOffsetX = e.clientX - rect.left;
      dragOffsetY = e.clientY - rect.top;
    });

    window.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      const newLeft = Math.max(4, Math.min(window.innerWidth - 80, e.clientX - dragOffsetX));
      const newTop = Math.max(4, Math.min(window.innerHeight - 36, e.clientY - dragOffsetY));
      hud.style.left = newLeft + "px";
      hud.style.top = newTop + "px";
    });

    window.addEventListener("mouseup", () => {
      if (isDragging) {
        isDragging = false;
        dragHandle.style.cursor = "grab";
        saveUiState();
      }
    });

    document.getElementById("rt-mode-select").addEventListener("change", (e) => {
      detectedMode = e.target.value;
      sendStatusToBot();
    });

    document.getElementById("rt-bind-call").addEventListener("click", (e) => {
      e.stopPropagation();
      mappingTarget = "CALL";
      updateHudStatus("👉 Clique no botão VERDE (CALL)!", "#fbbf24");
    });

    document.getElementById("rt-bind-put").addEventListener("click", (e) => {
      e.stopPropagation();
      mappingTarget = "PUT";
      updateHudStatus("👉 Clique no botão VERMELHO (PUT)!", "#fbbf24");
    });

    document.getElementById("rt-bind-invest").addEventListener("click", (e) => {
      e.stopPropagation();
      mappingTarget = "INVEST";
      updateHudStatus("👉 Clique sobre o número no campo Invest (R$)!", "#fbbf24");
    });

    document.getElementById("rt-bind-nova").addEventListener("click", (e) => {
      e.stopPropagation();
      mappingTarget = "NOVA_OPCAO";
      updateHudStatus("👉 Clique em '+ NOVA OPÇÃO'!", "#fbbf24");
    });

    function refreshHudBoundBadges() {
      autoDeriveAuxTargets();
      if (boundTargets.CALL) {
        const bCall = document.getElementById("rt-bind-call");
        if (bCall) {
          bCall.textContent = "CALL ✓";
          bCall.style.outline = "1.5px solid #fff";
        }
      }
      if (boundTargets.PUT) {
        const bPut = document.getElementById("rt-bind-put");
        if (bPut) {
          bPut.textContent = "PUT ✓";
          bPut.style.outline = "1.5px solid #fff";
        }
      }
      if (boundTargets.INVEST) {
        const bInv = document.getElementById("rt-bind-invest");
        if (bInv) bInv.textContent = `Invest ✓`;
      }
      if (boundTargets.NOVA_OPCAO) {
        const bNov = document.getElementById("rt-bind-nova");
        if (bNov) bNov.textContent = `Nova Opção ✓`;
      }
    }

    refreshHudBoundBadges();

    document.getElementById("rt-reset-price").addEventListener("click", (e) => {
      e.stopPropagation();
      lastValidPrice = null;
      activeAssetId = null;
      outlierStreakCount = 0;
      if (bridgeSocket && bridgeSocket.readyState === WebSocket.OPEN) {
        bridgeSocket.send(JSON.stringify({ type: "RESET_CANDLES" }));
      }
      sendStatusToBot();
      updateHudStatus("Ressincronizado!", "#10b981");
    });

    window.addEventListener(
      "mousedown",
      (e) => {
        if (!mappingTarget) return;
        if (hud.contains(e.target)) return;

        e.preventDefault();
        e.stopPropagation();

        const targetName = mappingTarget;
        mappingTarget = null;

        const clickableAncestor =
          e.target.closest("button, [role='button'], a, [class*='btn'], [class*='button']") ||
          e.target;

        boundTargets[targetName] = {
          el: clickableAncestor,
          rawEl: e.target,
          clientX: e.clientX,
          clientY: e.clientY,
          screenX: e.screenX,
          screenY: e.screenY,
          auto: false,
        };

        autoDeriveAuxTargets();
        saveBoundTargetsToStorage();
        refreshHudBoundBadges();

        updateHudStatus(`✅ ${targetName} OK!`, "#10b981");
        sendStatusToBot();
      },
      true
    );
  }

  function updateHudStatus(text, color) {
    const el = document.getElementById("rt-hud-status");
    if (el) {
      el.textContent = text;
      el.title = text;
      el.style.color = color || "#94a3b8";
    }
  }

  function sendStatusToBot() {
    autoDeriveAuxTargets();
    if (bridgeSocket && bridgeSocket.readyState === WebSocket.OPEN) {
      const { domBalance } = readSafirionDomState();
      bridgeSocket.send(
        JSON.stringify({
          type: "BRIDGE_STATUS",
          url: window.location.href,
          sub_type: detectedMode,
          balance: domBalance,
          price: lastValidPrice,
          call_bound: Boolean(boundTargets.CALL),
          put_bound: Boolean(boundTargets.PUT),
        })
      );
    }
  }

  function performAutoResync() {
    activeAssetId = null;
    outlierStreakCount = 0;
    autoDeriveAuxTargets();
    if (findDomButtonByKeyword("NOVAOPCAO")) {
      unlockNovaOpcaoNow();
    }
    closeDuplicateAssetTabs();
    sendStatusToBot();
    updateHudStatus("🔄 Auto-Sync ativo!", "#10b981");
  }

  function connectToBotServer() {
    if (isConnectingWs) return;
    if (bridgeSocket && (bridgeSocket.readyState === WebSocket.OPEN || bridgeSocket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    isConnectingWs = true;
    try {
      const ws = new WebSocket(BRIDGE_WS_URL);
      bridgeSocket = ws;
      window.__ROBO_TRADER_BRIDGE_SOCKET__ = ws;

      ws.onopen = () => {
        isConnectingWs = false;
        lastServerPongAtMs = Date.now();
        updateHudStatus("Conectado ao Painel!", "#10b981");
        sendStatusToBot();
      };

      ws.onmessage = async (event) => {
        lastServerPongAtMs = Date.now();
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "SERVER_PONG") {
            return;
          } else if (msg.type === "AUTO_RESYNC") {
            performAutoResync();
          } else if (msg.type === "UNLOCK_NOVA_OPCAO") {
            needsNovaOpcaoUnlock = true;
            unlockNovaOpcaoNow();
          } else if (msg.type === "SET_INVEST_AMOUNT") {
            await setSafirionInvestmentAmount(msg.amount);
          } else if (msg.type === "EXECUTE_ORDER") {
            const res = await executeOrderInBackground(
              msg.direction,
              msg.amount,
              msg.sub_type || detectedMode,
              msg.expiration || 1
            );
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(
                JSON.stringify({
                  type: "ORDER_RESULT",
                  order_id: msg.order_id,
                  direction: msg.direction,
                  sub_type: msg.sub_type || detectedMode,
                  ...res,
                })
              );
            }
          }
        } catch (err) {}
      };

      ws.onerror = () => {
        isConnectingWs = false;
      };

      ws.onclose = () => {
        isConnectingWs = false;
        if (bridgeSocket === ws) {
          updateHudStatus("Reconectando...", "#f59e0b");
          setTimeout(connectToBotServer, 2000);
        }
      };
    } catch (e) {
      isConnectingWs = false;
      setTimeout(connectToBotServer, 2000);
    }
  }

  createFloatingHud();
  connectToBotServer();
})();

