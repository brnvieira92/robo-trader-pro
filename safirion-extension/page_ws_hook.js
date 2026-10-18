/**
 * Injetado em document_start no mundo MAIN (antes de qualquer script da Safirion rodar).
 * 1. Intercepta 100% dos WebSockets abertos pelo site trade.safirion.com/traderoom.
 * 2. Executa cliques diretamente nas propriedades internas do React (__reactProps / __reactFiber)
 *    no mundo MAIN, garantindo que o auto-clique em "Nova Opção", "CALL" e "PUT" funcione
 *    100% em segundo plano mesmo com a janela do navegador minimizada.
 */
(function () {
  if (window.__SAFIRION_MAIN_WS_HOOK__) return;
  window.__SAFIRION_MAIN_WS_HOOK__ = true;

  const OrigWebSocket = window.WebSocket;

  window.WebSocket = function (url, protocols) {
    const ws = protocols ? new OrigWebSocket(url, protocols) : new OrigWebSocket(url);

    ws.addEventListener("message", function (event) {
      try {
        if (typeof event.data === "string") {
          window.postMessage(
            {
              source: "SAFIRION_WS_INTERCEPT",
              url: url,
              payload: event.data,
            },
            "*"
          );
        }
      } catch (e) {}
    });

    return ws;
  };

  window.WebSocket.prototype = OrigWebSocket.prototype;
  Object.assign(window.WebSocket, OrigWebSocket);

  // Executor de cliques e duplo-clique (dblclick) React Fiber no mundo MAIN (funciona mesmo com tela minimizada)
  function fireReactClickOnNode(node, clientX, clientY, isDoubleClick) {
    if (!node || node === document.body || node === document.documentElement) return false;
    let triggered = false;

    const makeFakeEvent = (detailNum, buttonsNum, nativeType) => ({
      bubbles: true,
      cancelable: true,
      defaultPrevented: false,
      isTrusted: true,
      view: window,
      detail: detailNum,
      clientX: clientX || 0,
      clientY: clientY || 0,
      screenX: clientX || 0,
      screenY: clientY || 0,
      button: 0,
      buttons: buttonsNum,
      pointerId: 1,
      pointerType: "mouse",
      isPrimary: true,
      target: node,
      currentTarget: node,
      preventDefault: function () {},
      stopPropagation: function () {},
      persist: function () {},
      nativeEvent: new MouseEvent(nativeType, {
        bubbles: true,
        cancelable: true,
        detail: detailNum,
        clientX: clientX || 0,
        clientY: clientY || 0,
        button: 0,
      }),
    });

    const evDown1 = makeFakeEvent(1, 1, "mousedown");
    const evUp1 = makeFakeEvent(1, 0, "mouseup");
    const evClick1 = makeFakeEvent(1, 0, "click");
    const evDown2 = makeFakeEvent(2, 1, "mousedown");
    const evUp2 = makeFakeEvent(2, 0, "mouseup");
    const evClick2 = makeFakeEvent(2, 0, "click");
    const evDbl = makeFakeEvent(2, 0, "dblclick");

    try {
      const keys = Object.keys(node);
      for (const k of keys) {
        if (k.startsWith("__reactProps") || k.startsWith("__reactEventHandlers")) {
          const props = node[k];
          if (props && typeof props === "object") {
            if (typeof props.onPointerDown === "function") {
              try { props.onPointerDown(evDown1); triggered = true; } catch (e) {}
            }
            if (typeof props.onMouseDown === "function") {
              try { props.onMouseDown(evDown1); triggered = true; } catch (e) {}
            }
            if (typeof props.onPointerUp === "function") {
              try { props.onPointerUp(evUp1); triggered = true; } catch (e) {}
            }
            if (typeof props.onMouseUp === "function") {
              try { props.onMouseUp(evUp1); triggered = true; } catch (e) {}
            }
            if (typeof props.onClick === "function") {
              try { props.onClick(evClick1); triggered = true; } catch (e) {}
            }
            if (isDoubleClick) {
              if (typeof props.onMouseDown === "function") {
                try { props.onMouseDown(evDown2); } catch (e) {}
              }
              if (typeof props.onMouseUp === "function") {
                try { props.onMouseUp(evUp2); } catch (e) {}
              }
              if (typeof props.onClick === "function") {
                try { props.onClick(evClick2); } catch (e) {}
              }
              if (typeof props.onDoubleClick === "function") {
                try { props.onDoubleClick(evDbl); triggered = true; } catch (e) {}
              }
            }
          }
        } else if (k.startsWith("__reactFiber") || k.startsWith("__reactInternalInstance")) {
          let fiber = node[k];
          for (let depth = 0; depth < 6 && fiber; depth++) {
            const mp = fiber.memoizedProps;
            if (mp && typeof mp === "object") {
              if (isDoubleClick && typeof mp.onDoubleClick === "function") {
                try { mp.onDoubleClick(evDbl); triggered = true; } catch (e) {}
                break;
              } else if (typeof mp.onClick === "function") {
                try { mp.onClick(evClick1); triggered = true; } catch (e) {}
                if (isDoubleClick) {
                  try { mp.onClick(evClick2); } catch (e) {}
                }
                break;
              } else if (typeof mp.onMouseDown === "function") {
                try { mp.onMouseDown(evDown1); triggered = true; } catch (e) {}
                break;
              }
            }
            fiber = fiber.return;
          }
        }
      }
    } catch (e) {}

    return triggered;
  }

  function findRightPanelInvestInput(hud) {
    const inputs = Array.from(document.querySelectorAll("input, textarea, [contenteditable='true']")).filter(
      (inp) =>
        (!hud || !hud.contains(inp)) &&
        inp.type !== "checkbox" &&
        inp.type !== "radio" &&
        inp.type !== "hidden"
    );

    // Filtra inputs da barra lateral direita (x > 60% da tela, topo < 300px) que NÃO sejam o campo de Expiração (08:51)
    const rightInputs = inputs.filter((inp) => {
      const r = inp.getBoundingClientRect();
      const val = (inp.value || inp.textContent || "").trim();
      const isTimeField = /\d{1,2}:\d{2}/.test(val);
      return r.left > window.innerWidth * 0.6 && r.top >= 0 && r.top < 300 && r.width > 5 && !isTimeField;
    });

    // Ordena priorizando o campo mais ao topo (Invest fica acima de Expiração) ou que contenha R$ / Invest
    rightInputs.sort((a, b) => {
      const aHasRs = /R\$|Invest/i.test((a.value || "") + " " + (a.parentElement?.textContent || ""));
      const bHasRs = /R\$|Invest/i.test((b.value || "") + " " + (b.parentElement?.textContent || ""));
      if (aHasRs && !bHasRs) return -1;
      if (!aHasRs && bHasRs) return 1;
      return a.getBoundingClientRect().top - b.getBoundingClientRect().top;
    });

    if (rightInputs.length > 0) return rightInputs[0];

    if (
      document.activeElement &&
      (document.activeElement.tagName === "INPUT" || document.activeElement.isContentEditable) &&
      (!hud || !hud.contains(document.activeElement))
    ) {
      return document.activeElement;
    }

    return null;
  }

  // Dispara handlers de teclado/change/blur diretamente nas props do React (__reactProps e __reactFiber)
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

  // Função auxiliar que força a atualização de um <input> controlado pelo React 16/17/18
  function forceReactInputValue(inputEl, newValueStr, lastChar) {
    const prevValue = inputEl.value;
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value"
    )?.set;

    // CRÍTICO para React: _valueTracker DEVE receber o valor ANTIGO antes do nativeSetter mudar para o valor NOVO!
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

    const syntheticChange = {
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
    };
    invokeReactHandlersOnChain(inputEl, ["onInput", "onChange"], syntheticChange);
  }

  // Altera o valor do campo "Invest" (R$) dando um DOUBLE CLICK na entrada e digitando o novo valor diretamente
  async function setReactInputValueAndType(targetAmount, clientX, clientY) {
    const valStr = String(Math.max(5, Math.round(Number(targetAmount) || 5)));
    try {
      const hud = document.getElementById("rt-safirion-hud");
      let investInput = findRightPanelInvestInput(hud);

      // Coordenada exata da caixa "Invest"
      let cx = clientX || Math.round(window.innerWidth - 75);
      let cy = clientY || 138;
      if (investInput) {
        const r = investInput.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
          cx = Math.round(r.left + r.width / 2);
          cy = Math.round(r.top + r.height / 2);
        }
      }

      const ptEl = document.elementFromPoint(cx, cy);
      const clickTarget = investInput || (ptEl && (!hud || !hud.contains(ptEl)) ? ptEl : null);

      // PASSO 1: Executa o DOUBLE CLICK (Clique 1 + Clique 2 + dblclick) no campo Invest para selecioná-lo
      if (clickTarget) {
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

        let curr = clickTarget;
        for (let i = 0; i < 4 && curr && curr !== document.body; i++) {
          try {
            curr.dispatchEvent(mkMouse("mousedown", 1, 1));
            curr.dispatchEvent(mkMouse("mouseup", 1, 0));
            curr.dispatchEvent(mkMouse("click", 1, 0));
            curr.dispatchEvent(mkMouse("mousedown", 2, 1));
            curr.dispatchEvent(mkMouse("mouseup", 2, 0));
            curr.dispatchEvent(mkMouse("click", 2, 0));
            curr.dispatchEvent(mkMouse("dblclick", 2, 0));
            fireReactClickOnNode(curr, cx, cy, true);
          } catch (e) {}
          curr = curr.parentElement;
        }
      }

      // Aguarda 20ms para o double-click focar/selecionar o input
      await new Promise((r) => setTimeout(r, 20));
      investInput = findRightPanelInvestInput(hud) || investInput;
      const activeNode = investInput || document.activeElement || clickTarget || document.body;

      // PASSO 2: Com o campo selecionado pelo double-click, digita os novos dígitos substituindo o valor!
      if (investInput && investInput.tagName === "INPUT") {
        const initialRawValue = investInput.value || "";
        const hadRsPrefix = /R\$/i.test(initialRawValue);

        investInput.focus();
        if (typeof investInput.select === "function") {
          investInput.select();
        }
        try {
          investInput.setSelectionRange(0, initialRawValue.length);
        } catch (e) {}

        // Digita dígito por dígito (ex: "1", depois "10") disparando KeyDown + _valueTracker + InputEvent + React onChange
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

          // Atualiza o valor no input via _valueTracker + nativeSetter + React onChange
          forceReactInputValue(investInput, partialDigits, ch);

          investInput.dispatchEvent(new KeyboardEvent("keyup", keyPayload));
          invokeReactHandlersOnChain(investInput, ["onKeyUp"], keyPayload);
        }

        // Verifica se a Safirion exige o prefixo "R$ " no value ou se aceitou os dígitos puros
        const afterDigits = (investInput.value || "").replace(/[^\d]/g, "");
        if (afterDigits !== valStr) {
          forceReactInputValue(investInput, hadRsPrefix ? `R$ ${valStr}` : valStr, valStr);
        }

        // PASSO 3: Pressiona Enter e dispara Blur/Change para a Safirion confirmar o novo valor
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
        // Caso o campo Invest seja um componente customizado (div/span/canvas) que fica selecionado após o double-click:
        // Dispara os dígitos tanto no DOM/window quanto nas props React (onKeyDown/onKeyPress/onInput) da árvore do elemento!
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
    } catch (e) {}
  }

  window.__RT_SET_INVEST_MAIN__ = setReactInputValueAndType;

  document.addEventListener("rt-main-world-set-invest", function (ev) {
    try {
      const detail = ev.detail ? (typeof ev.detail === "string" ? JSON.parse(ev.detail) : ev.detail) : {};
      if (detail.amount) {
        setReactInputValueAndType(detail.amount, detail.clientX, detail.clientY);
      }
    } catch (e) {}
  });

  document.addEventListener("rt-main-world-click", function (ev) {
    try {
      const detail = ev.detail ? (typeof ev.detail === "string" ? JSON.parse(ev.detail) : ev.detail) : {};
      const cx = detail.clientX || 0;
      const cy = detail.clientY || 0;
      const isDbl = detail.kind === "DBLCLICK_INVEST";

      const marked = document.querySelectorAll('[data-rt-click-target="1"]');
      const visited = new Set();

      marked.forEach((el) => {
        el.removeAttribute("data-rt-click-target");
        let curr = el;
        for (let i = 0; i < 5 && curr && curr !== document.body; i++) {
          if (!visited.has(curr)) {
            visited.add(curr);
            fireReactClickOnNode(curr, cx, cy, isDbl);
          }
          curr = curr.parentElement;
        }
      });
    } catch (e) {}
  });
})();
