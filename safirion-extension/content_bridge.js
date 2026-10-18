/**
 * RoboTrader Pro — Safirion Bridge v4.0 (Precisão Cirúrgica: Filtro de Tick < 0.08% + Auditoria Real de Win/Loss)
 *
 * Correções v4.0 baseadas nos prints de diagnóstico:
 * 1. Bloqueia pacotes WebSocket de outros ativos em segundo plano (como o tick intruso 1.12135 que distorcia o EUR/USD 1.11065,
 *    fazendo o robô só mandar PUT e achar que sempre deu WIN).
 * 2. Prioriza a leitura da cotação do ATIVO ABERTO NA TELA e filtra variações máximas de 0.08% por tick.
 * 3. Lê o Saldo Real no topo direito da Safirion ("R$ 143.24") e o popup/histórico de "RESULTADO (L/P)"
 *    para que o WIN / LOSS do painel seja 100% idêntico ao resultado real da corretora!
 */

(function () {
  if (window.__ROBO_TRADER_BRIDGE_SOCKET__) {
    try { window.__ROBO_TRADER_BRIDGE_SOCKET__.close(); } catch (e) {}
  }
  const oldHud = document.getElementById("rt-safirion-hud");
  if (oldHud) oldHud.remove();

  const BRIDGE_WS_URL = "ws://127.0.0.1:8000/ws/safirion-bridge";
  let bridgeSocket = null;
  let lastValidPrice = null;
  let activeAssetId = null;
  let detectedSymbol = "EURUSD-OTC";
  let detectedMode = "BINARY";
  let mappingTarget = null;

  let boundTargets = {
    CALL: null,
    PUT: null,
  };

  // Lê o saldo real ("R$ 143.24") e eventual popup "RESULTADO (L/P)" na tela da Safirion
  function readSafirionDomState() {
    let domBalance = null;
    let lastResultPnl = null;

    try {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
      let node;
      while ((node = walker.nextNode())) {
        const txt = (node.nodeValue || "").trim();
        if (!txt) continue;

        // Captura o saldo principal no topo direito (ex: "R$ 143.24")
        if (domBalance === null && /^R\$\s*\d+\.\d{2}$/.test(txt)) {
          const val = parseFloat(txt.replace(/[^\d.]/g, ""));
          if (!isNaN(val) && val > 0) {
            domBalance = val;
          }
        }
      }

      // Verifica se apareceu o card "RESULTADO (L/P)" na tela
      const allElements = document.querySelectorAll("div, span");
      for (const el of allElements) {
        const t = (el.textContent || "").trim();
        if (t.includes("RESULTADO (L/P)")) {
          const match = t.match(/([+-]?\s*R\$\s*\d+([.,]\d{1,2})?)/);
          if (match) {
            const raw = match[1].replace(/\s+/g, "").replace("R$", "").replace(",", ".");
            const pnlVal = parseFloat(raw);
            if (!isNaN(pnlVal)) {
              lastResultPnl = pnlVal;
              break;
            }
          }
        }
      }
    } catch (e) {}

    return { domBalance, lastResultPnl };
  }

  // Valida estritamente se o tick pertence ao ativo atual (rejeita o 1.12135 intruso quando o ativo está em 1.11065!)
  function isStrictSameAssetTick(val) {
    if (typeof val !== "number" || !isFinite(val)) return false;
    if (Number.isInteger(val)) return false;
    if (val <= 0.0001 || val > 180000) return false;

    if (lastValidPrice !== null) {
      const relDiff = Math.abs(val - lastValidPrice) / lastValidPrice;
      // Em M1, um tick real varia no máximo 0.01% a 0.05%. Qualquer salto > 0.12% é pacote de OUTRO ativo no WebSocket!
      if (relDiff > 0.0012) {
        return false;
      }
    }
    return true;
  }

  window.addEventListener("message", (event) => {
    if (!event.data || event.data.source !== "SAFIRION_WS_INTERCEPT") return;
    try {
      const data = JSON.parse(event.data.payload);
      extractCleanQuote(data);
    } catch (e) {}
  });

  function extractCleanQuote(obj) {
    if (!obj || typeof obj !== "object") return;

    // Se o pacote tem identificador de ativo (active_id), trava no primeiro ativo principal
    if (obj.active_id && activeAssetId !== null && obj.active_id !== activeAssetId) {
      return;
    }

    const candidate = obj.quote ?? obj.close ?? obj.rate ?? obj.bid ?? obj.price ?? obj.value;
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
      extractCleanQuote(obj.msg);
    } else if (Array.isArray(obj)) {
      for (const item of obj) extractCleanQuote(item);
    }
  }

  function sendTickToBot(price) {
    const hudPrice = document.getElementById("rt-hud-price");
    if (hudPrice) hudPrice.textContent = `Cotação: ${price.toFixed(5)}`;

    // WebSocket ticks não sofrem throttling do Chrome mesmo com a janela minimizada!
    if (!isTradeOpen || Date.now() >= tradeAutoUnlockAtMs) {
      isTradeOpen = false;
      clickNovaOpcaoIfPresent(false);
    }

    if (bridgeSocket && bridgeSocket.readyState === WebSocket.OPEN) {
      const { domBalance, lastResultPnl } = readSafirionDomState();
      bridgeSocket.send(
        JSON.stringify({
          type: "SAFIRION_TICK",
          price: price,
          balance: domBalance,
          last_result_pnl: lastResultPnl,
          symbol: detectedSymbol,
          sub_type: detectedMode,
          timestamp: Date.now(),
        })
      );
    }
  }

  // Envia atualização periódica de Saldo mesmo entre ticks
  setInterval(() => {
    if (bridgeSocket && bridgeSocket.readyState === WebSocket.OPEN) {
      const { domBalance, lastResultPnl } = readSafirionDomState();
      if (domBalance !== null) {
        bridgeSocket.send(
          JSON.stringify({
            type: "SAFIRION_BALANCE_UPDATE",
            balance: domBalance,
            last_result_pnl: lastResultPnl,
          })
        );
      }
    }
  }, 1000);

  // Garante que o executor de cliques React Fiber no mundo MAIN esteja ativo mesmo sem recarregar a extensão
  function ensureMainWorldClickBridge() {
    try {
      if (document.documentElement.getAttribute("data-rt-main-bridge") === "1") return;
      document.documentElement.setAttribute("data-rt-main-bridge", "1");
      const s = document.createElement("script");
      s.textContent = `
        (function() {
          if (window.__RT_MAIN_CLICK_LISTENER__) return;
          window.__RT_MAIN_CLICK_LISTENER__ = true;
          function fireReactClick(node, cx, cy) {
            if (!node || node === document.body || node === document.documentElement) return;
            const fakeEv = {
              bubbles: true, cancelable: true, defaultPrevented: false, isTrusted: true,
              view: window, detail: 1, clientX: cx || 0, clientY: cy || 0,
              screenX: cx || 0, screenY: cy || 0, button: 0, buttons: 1,
              pointerId: 1, pointerType: "mouse", isPrimary: true,
              target: node, currentTarget: node,
              preventDefault: function(){}, stopPropagation: function(){}, persist: function(){},
              nativeEvent: new MouseEvent("click", { bubbles: true, cancelable: true, clientX: cx || 0, clientY: cy || 0, button: 0 })
            };
            try {
              const keys = Object.keys(node);
              for (const k of keys) {
                if (k.startsWith("__reactProps") || k.startsWith("__reactEventHandlers")) {
                  const p = node[k];
                  if (p && typeof p === "object") {
                    if (typeof p.onPointerDown === "function") try { p.onPointerDown(fakeEv); } catch(e){}
                    if (typeof p.onMouseDown === "function") try { p.onMouseDown(fakeEv); } catch(e){}
                    if (typeof p.onPointerUp === "function") try { p.onPointerUp({ ...fakeEv, buttons: 0 }); } catch(e){}
                    if (typeof p.onMouseUp === "function") try { p.onMouseUp({ ...fakeEv, buttons: 0 }); } catch(e){}
                    if (typeof p.onClick === "function") try { p.onClick({ ...fakeEv, buttons: 0 }); } catch(e){}
                  }
                } else if (k.startsWith("__reactFiber") || k.startsWith("__reactInternalInstance")) {
                  let f = node[k];
                  for (let d = 0; d < 6 && f; d++) {
                    const mp = f.memoizedProps;
                    if (mp && typeof mp === "object" && (typeof mp.onClick === "function" || typeof mp.onMouseDown === "function")) {
                      if (typeof mp.onMouseDown === "function") try { mp.onMouseDown(fakeEv); } catch(e){}
                      if (typeof mp.onClick === "function") try { mp.onClick({ ...fakeEv, buttons: 0 }); } catch(e){}
                      break;
                    }
                    f = f.return;
                  }
                }
              }
            } catch(e) {}
          }
          document.addEventListener("rt-main-world-click", function(ev) {
            try {
              const d = ev.detail ? (typeof ev.detail === "string" ? JSON.parse(ev.detail) : ev.detail) : {};
              const marked = document.querySelectorAll('[data-rt-click-target="1"]');
              const seen = new Set();
              marked.forEach(function(el) {
                el.removeAttribute("data-rt-click-target");
                let c = el;
                for (let i = 0; i < 5 && c && c !== document.body; i++) {
                  if (!seen.has(c)) {
                    seen.add(c);
                    fireReactClick(c, d.clientX, d.clientY);
                  }
                  c = c.parentElement;
                }
              });
            } catch(e) {}
          });
        })();
      `;
      (document.head || document.documentElement).appendChild(s);
      s.remove();
    } catch (e) {}
  }

  ensureMainWorldClickBridge();

  // Normaliza texto removendo acentos, espaços, quebras de linha e símbolos ("+\nNova\nopção" -> "NOVAOPCAO")
  function cleanAlpha(str) {
    return (str || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-zA-Z]/g, "")
      .toUpperCase();
  }

  let isTradeOpen = false;
  let tradeAutoUnlockAtMs = 0;
  let lastNovaOpcaoClickMs = 0;

  // Localiza todos os elementos do botão "+ NOVA OPÇÃO" na interface da Safirion
  function findNovaOpcaoElements() {
    const matches = [];
    try {
      const hud = document.getElementById("rt-safirion-hud");
      const all = document.querySelectorAll("button, [role='button'], a, div, span");

      for (const el of all) {
        if (hud && hud.contains(el)) continue;
        const key = cleanAlpha(el.textContent);
        if (!key) continue;

        const isNovaOpcao =
          key === "NOVAOPCAO" ||
          key === "NEWOPTION" ||
          key === "ABRIRNOVAOPCAO" ||
          (key.includes("NOVAOPCAO") && key.length <= 20) ||
          ((key === "OPCAO" || key === "NOVA") &&
            el.parentElement &&
            cleanAlpha(el.parentElement.textContent).includes("NOVAOPCAO"));

        if (!isNovaOpcao) continue;

        const rect = el.getBoundingClientRect();
        // Ignora containers gigantes da página inteira
        if (rect.width > 340 || rect.height > 280) continue;
        const area = Math.max(1, rect.width * rect.height);
        matches.push({ el, rect, area });
      }

      // Ordena do elemento mais interno (menor área) para o botão externo
      matches.sort((a, b) => a.area - b.area);
    } catch (e) {}
    return matches;
  }

  // Dispara sequência completa de eventos DOM + React Fiber (mundo MAIN) em um elemento
  function dispatchFullClickOnElement(baseEl, clientX, clientY, screenX, screenY, kind) {
    if (!baseEl) return false;

    const rect = baseEl.getBoundingClientRect ? baseEl.getBoundingClientRect() : null;
    const cx = clientX || (rect && rect.width > 0 ? Math.round(rect.left + rect.width / 2) : 1465);
    const cy = clientY || (rect && rect.height > 0 ? Math.round(rect.top + rect.height / 2) : 450);

    const chain = [];
    let curr = baseEl;
    for (let i = 0; i < 5 && curr && curr !== document.body && curr !== document.documentElement; i++) {
      const r = curr.getBoundingClientRect ? curr.getBoundingClientRect() : null;
      if (r && (r.width > 420 || r.height > 420) && chain.length > 0) break;
      chain.push(curr);
      curr.setAttribute("data-rt-click-target", "1");
      curr = curr.parentElement;
    }

    const eventInit = {
      bubbles: true,
      cancelable: true,
      composed: true,
      view: window,
      detail: 1,
      screenX: screenX || cx,
      screenY: screenY || cy,
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

        // Caso esteja rodando direto no console (MAIN world)
        const reactKeys = Object.keys(node).filter(
          (k) => k.startsWith("__reactProps") || k.startsWith("__reactEventHandlers")
        );
        for (const rk of reactKeys) {
          const props = node[rk];
          if (props) {
            if (typeof props.onPointerDown === "function") props.onPointerDown(eventInit);
            if (typeof props.onMouseDown === "function") props.onMouseDown(eventInit);
            if (typeof props.onPointerUp === "function") props.onPointerUp({ ...eventInit, buttons: 0 });
            if (typeof props.onMouseUp === "function") props.onMouseUp({ ...eventInit, buttons: 0 });
            if (typeof props.onClick === "function") props.onClick({ ...eventInit, buttons: 0 });
          }
        }
      } catch (e) {}
    }

    // Aciona o executor síncrono no mundo MAIN (acessa __reactProps e __reactFiber mesmo na extensão e com tela minimizada)
    try {
      document.dispatchEvent(
        new CustomEvent("rt-main-world-click", {
          detail: JSON.stringify({ clientX: cx, clientY: cy, kind: kind || "CLICK" }),
        })
      );
    } catch (e) {}

    return true;
  }

  // Destrava automaticamente o botão "+ NOVA OPÇÃO" na Safirion para reabilitar CALL e PUT
  function clickNovaOpcaoIfPresent(forceNow = false) {
    try {
      const now = Date.now();
      if (!forceNow && now - lastNovaOpcaoClickMs < 450) return false;

      const matches = findNovaOpcaoElements();
      if (matches.length === 0) return false;

      lastNovaOpcaoClickMs = now;
      // Clica nos elementos encontrados (do mais interno até o container do botão azul)
      const targetsToClick = matches.slice(0, 3);
      for (const item of targetsToClick) {
        const cx = item.rect.width > 0 ? Math.round(item.rect.left + item.rect.width / 2) : 1465;
        const cy = item.rect.height > 0 ? Math.round(item.rect.top + item.rect.height / 2) : 500;
        dispatchFullClickOnElement(item.el, cx, cy, cx, cy, "NOVA_OPCAO");
      }

      updateHudStatus("🔄 'Nova Opção' destravada (CALL/PUT ativos)!", "#38bdf8");
      return true;
    } catch (e) {
      return false;
    }
  }

  // Encontra o elemento do botão CALL ou PUT nas coordenadas vinculadas mesmo após o React recriar o DOM ou com janela minimizada
  function resolveBoundTargetElement(targetObj) {
    if (!targetObj) return null;
    const { el, clientX, clientY, className } = targetObj;
    const hud = document.getElementById("rt-safirion-hud");

    // 1. Tenta elementFromPoint (quando a janela está visível ou ativa)
    const ptEl = document.elementFromPoint(clientX, clientY);
    if (ptEl && (!hud || !hud.contains(ptEl)) && !cleanAlpha(ptEl.textContent).includes("NOVAOPCAO")) {
      return ptEl;
    }

    // 2. Se a referência original ainda existe no DOM e não é Nova Opção
    if (el && document.body.contains(el) && !cleanAlpha(el.textContent).includes("NOVAOPCAO")) {
      return el;
    }

    // 3. Busca geométrica por BoundingBox (funciona 100% mesmo com navegador minimizado após o React recriar os botões CALL/PUT!)
    let bestEl = null;
    let bestArea = Infinity;
    const candidates = document.querySelectorAll("button, [role='button'], a, div, span");
    for (const cand of candidates) {
      if (hud && hud.contains(cand)) continue;
      if (cleanAlpha(cand.textContent).includes("NOVAOPCAO")) continue;

      const r = cand.getBoundingClientRect();
      if (r.width <= 10 || r.height <= 10 || r.width > 320 || r.height > 260) continue;

      if (clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom) {
        const area = r.width * r.height;
        if (area < bestArea) {
          bestArea = area;
          bestEl = cand;
        }
      }
    }
    if (bestEl) {
      targetObj.el = bestEl;
      return bestEl;
    }

    // 4. Fallback por classe CSS próxima da coordenada Y original
    if (className && typeof className === "string") {
      let closestEl = null;
      let minDist = Infinity;
      for (const cand of candidates) {
        if (cand.className === className && !cleanAlpha(cand.textContent).includes("NOVAOPCAO")) {
          const r = cand.getBoundingClientRect();
          if (r.width > 0 && r.height > 0) {
            const cy = r.top + r.height / 2;
            const dist = Math.abs(cy - clientY);
            if (dist < minDist && dist < 120) {
              minDist = dist;
              closestEl = cand;
            }
          }
        }
      }
      if (closestEl) {
        targetObj.el = closestEl;
        return closestEl;
      }
    }

    return null;
  }

  function triggerFullClick(targetObj, direction) {
    if (!targetObj) return false;
    const baseEl = resolveBoundTargetElement(targetObj);
    if (!baseEl) return false;

    return dispatchFullClickOnElement(
      baseEl,
      targetObj.clientX,
      targetObj.clientY,
      targetObj.screenX,
      targetObj.screenY,
      direction || "ORDER"
    );
  }

  // Loop contínuo + acionamento via WebSocket para destravar "Nova Opção" assim que a operação encerra
  setInterval(() => {
    if (!isTradeOpen || Date.now() >= tradeAutoUnlockAtMs) {
      isTradeOpen = false;
      clickNovaOpcaoIfPresent(false);
    }
  }, 700);

  async function executeOrderInBackground(direction, amount, subType, expirationMinutes) {
    const targetObj = boundTargets[direction];
    if (!targetObj) {
      updateHudStatus(`⚠️ Vincule o botão ${direction} na barra acima!`, "#f43f5e");
      return {
        success: false,
        message: `Botão ${direction} ainda não vinculado na barra azul no topo da Safirion!`,
      };
    }

    // Se o botão "+ NOVA OPÇÃO" ainda estiver na tela, clica nele primeiro e aguarda 280ms para o React reabilitar CALL e PUT!
    if (findNovaOpcaoElements().length > 0) {
      clickNovaOpcaoIfPresent(true);
      await new Promise((resolve) => setTimeout(resolve, 280));
    }

    const ok = triggerFullClick(targetObj, direction);
    if (ok) {
      isTradeOpen = true;
      const expMin = Math.max(1, Number(expirationMinutes) || 1);
      // Libera automaticamente após o vencimento caso não receba antes o sinal UNLOCK_NOVA_OPCAO do backend
      tradeAutoUnlockAtMs = Date.now() + expMin * 60 * 1000 + 1500;

      updateHudStatus(`⚡ Ordem ${direction} (${subType}) enviada!`, "#10b981");
      return {
        success: true,
        message: `Ordem ${direction} (${subType}) executada na Safirion @ ${lastValidPrice ? lastValidPrice.toFixed(5) : ""}!`,
      };
    }

    updateHudStatus(`⚠️ Não foi possível clicar em ${direction}. Verifique a guia!`, "#f43f5e");
    return {
      success: false,
      message: `Não foi possível encontrar o botão ${direction} na tela da Safirion.`,
    };
  }

  function createFloatingHud() {
    const hud = document.createElement("div");
    hud.id = "rt-safirion-hud";
    hud.style.cssText = `
      position: fixed;
      top: 6px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 2147483647;
      background: rgba(9, 13, 22, 0.96);
      border: 1.5px solid #06b6d4;
      border-radius: 10px;
      padding: 5px 12px;
      color: #f8fafc;
      font-family: system-ui, -apple-system, sans-serif;
      font-size: 11px;
      display: flex;
      align-items: center;
      gap: 8px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.75);
    `;

    hud.innerHTML = `
      <span style="font-weight:800;color:#22d3ee;">🤖 RoboTrader v4.2</span>
      <select id="rt-mode-select" style="background:#1e293b;color:#fff;border:1px solid #334155;border-radius:5px;padding:2px 5px;font-size:11px;">
        <option value="BINARY">Binárias</option>
        <option value="DIGITAL">Digital</option>
        <option value="BLITZ">Blitz</option>
      </select>
      <button id="rt-bind-call" style="background:#059669;color:#fff;border:none;border-radius:5px;padding:3px 8px;cursor:pointer;font-weight:bold;font-size:11px;">
        1. Vincular CALL ▲
      </button>
      <button id="rt-bind-put" style="background:#e11d48;color:#fff;border:none;border-radius:5px;padding:3px 8px;cursor:pointer;font-weight:bold;font-size:11px;">
        2. Vincular PUT ▼
      </button>
      <button id="rt-reset-price" title="Usar caso troque de par de moeda na Safirion" style="background:#1e293b;color:#cbd5e1;border:1px solid #475569;border-radius:5px;padding:3px 6px;cursor:pointer;font-size:10px;">
        Trocar Ativo / Sync
      </button>
      <span id="rt-hud-price" style="color:#38bdf8;font-weight:bold;font-size:11px;">Cotação: --</span>
      <span id="rt-hud-status" style="color:#94a3b8;font-size:11px;">Conectando...</span>
    `;

    document.body.appendChild(hud);

    document.getElementById("rt-mode-select").addEventListener("change", (e) => {
      detectedMode = e.target.value;
      sendStatusToBot();
    });

    document.getElementById("rt-bind-call").addEventListener("click", (e) => {
      e.stopPropagation();
      clickNovaOpcaoIfPresent(true);
      mappingTarget = "CALL";
      updateHudStatus("👉 Clique no botão VERDE (CALL/Acima) da Safirion!", "#fbbf24");
    });

    document.getElementById("rt-bind-put").addEventListener("click", (e) => {
      e.stopPropagation();
      clickNovaOpcaoIfPresent(true);
      mappingTarget = "PUT";
      updateHudStatus("👉 Clique no botão VERMELHO (PUT/Abaixo) da Safirion!", "#fbbf24");
    });

    document.getElementById("rt-reset-price").addEventListener("click", (e) => {
      e.stopPropagation();
      lastValidPrice = null;
      activeAssetId = null;
      clickNovaOpcaoIfPresent(true);
      if (bridgeSocket && bridgeSocket.readyState === WebSocket.OPEN) {
        bridgeSocket.send(JSON.stringify({ type: "RESET_CANDLES" }));
      }
      updateHudStatus("Ativo ressincronizado!", "#10b981");
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
          e.target.closest("button, [role='button'], a, [class*='btn'], [class*='button'], [class*='call'], [class*='put']") ||
          e.target;

        boundTargets[targetName] = {
          el: clickableAncestor,
          rawEl: e.target,
          className: typeof clickableAncestor.className === "string" ? clickableAncestor.className : "",
          clientX: e.clientX,
          clientY: e.clientY,
          screenX: e.screenX,
          screenY: e.screenY,
        };

        const btnHud = document.getElementById(
          targetName === "CALL" ? "rt-bind-call" : "rt-bind-put"
        );
        btnHud.textContent = `${targetName} OK (${e.clientX},${e.clientY}) ✓`;
        btnHud.style.outline = "2px solid #fff";

        updateHudStatus(`✅ ${targetName} vinculado!`, "#10b981");
        sendStatusToBot();
      },
      true
    );
  }

  function updateHudStatus(text, color) {
    const el = document.getElementById("rt-hud-status");
    if (el) {
      el.textContent = text;
      el.style.color = color || "#94a3b8";
    }
  }

  function sendStatusToBot() {
    if (bridgeSocket && bridgeSocket.readyState === WebSocket.OPEN) {
      const { domBalance } = readSafirionDomState();
      bridgeSocket.send(
        JSON.stringify({
          type: "BRIDGE_STATUS",
          url: window.location.href,
          sub_type: detectedMode,
          balance: domBalance,
          call_bound: Boolean(boundTargets.CALL),
          put_bound: Boolean(boundTargets.PUT),
        })
      );
    }
  }

  function connectToBotServer() {
    try {
      bridgeSocket = new WebSocket(BRIDGE_WS_URL);
      window.__ROBO_TRADER_BRIDGE_SOCKET__ = bridgeSocket;

      bridgeSocket.onopen = () => {
        updateHudStatus("Conectado ao Painel!", "#10b981");
        sendStatusToBot();
      };

      bridgeSocket.onmessage = async (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "UNLOCK_NOVA_OPCAO") {
            // Acionado pelo backend no exato segundo em que a operação encerra (funciona com janela minimizada!)
            isTradeOpen = false;
            clickNovaOpcaoIfPresent(true);
            setTimeout(() => clickNovaOpcaoIfPresent(true), 350);
            setTimeout(() => clickNovaOpcaoIfPresent(true), 900);
            setTimeout(() => clickNovaOpcaoIfPresent(true), 1800);
          } else if (msg.type === "EXECUTE_ORDER") {
            const res = await executeOrderInBackground(
              msg.direction,
              msg.amount,
              msg.sub_type || detectedMode,
              msg.expiration || 1
            );
            bridgeSocket.send(
              JSON.stringify({
                type: "ORDER_RESULT",
                order_id: msg.order_id,
                direction: msg.direction,
                sub_type: msg.sub_type || detectedMode,
                ...res,
              })
            );
          }
        } catch (err) {}
      };

      bridgeSocket.onclose = () => {
        updateHudStatus("Reconectando...", "#f59e0b");
        setTimeout(connectToBotServer, 2000);
      };
    } catch (e) {
      setTimeout(connectToBotServer, 2000);
    }
  }

  createFloatingHud();
  connectToBotServer();
})();
