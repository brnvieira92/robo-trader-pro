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

  // Executor de cliques React Fiber no mundo MAIN (funciona mesmo com tela minimizada)
  function fireReactClickOnNode(node, clientX, clientY) {
    if (!node || node === document.body || node === document.documentElement) return false;
    let triggered = false;

    const fakeEvent = {
      bubbles: true,
      cancelable: true,
      defaultPrevented: false,
      isTrusted: true,
      view: window,
      detail: 1,
      clientX: clientX || 0,
      clientY: clientY || 0,
      screenX: clientX || 0,
      screenY: clientY || 0,
      button: 0,
      buttons: 1,
      pointerId: 1,
      pointerType: "mouse",
      isPrimary: true,
      target: node,
      currentTarget: node,
      preventDefault: function () {},
      stopPropagation: function () {},
      persist: function () {},
      nativeEvent: new MouseEvent("click", {
        bubbles: true,
        cancelable: true,
        clientX: clientX || 0,
        clientY: clientY || 0,
        button: 0,
      }),
    };

    try {
      const keys = Object.keys(node);
      for (const k of keys) {
        if (k.startsWith("__reactProps") || k.startsWith("__reactEventHandlers")) {
          const props = node[k];
          if (props && typeof props === "object") {
            if (typeof props.onPointerDown === "function") {
              try { props.onPointerDown(fakeEvent); triggered = true; } catch (e) {}
            }
            if (typeof props.onMouseDown === "function") {
              try { props.onMouseDown(fakeEvent); triggered = true; } catch (e) {}
            }
            if (typeof props.onPointerUp === "function") {
              try { props.onPointerUp({ ...fakeEvent, buttons: 0 }); triggered = true; } catch (e) {}
            }
            if (typeof props.onMouseUp === "function") {
              try { props.onMouseUp({ ...fakeEvent, buttons: 0 }); triggered = true; } catch (e) {}
            }
            if (typeof props.onClick === "function") {
              try { props.onClick({ ...fakeEvent, buttons: 0 }); triggered = true; } catch (e) {}
            }
          }
        } else if (k.startsWith("__reactFiber") || k.startsWith("__reactInternalInstance")) {
          let fiber = node[k];
          for (let depth = 0; depth < 6 && fiber; depth++) {
            const mp = fiber.memoizedProps;
            if (mp && typeof mp === "object") {
              if (typeof mp.onClick === "function") {
                try { mp.onClick({ ...fakeEvent, buttons: 0 }); triggered = true; } catch (e) {}
                break;
              } else if (typeof mp.onMouseDown === "function") {
                try { mp.onMouseDown(fakeEvent); triggered = true; } catch (e) {}
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

  document.addEventListener("rt-main-world-click", function (ev) {
    try {
      const detail = ev.detail ? (typeof ev.detail === "string" ? JSON.parse(ev.detail) : ev.detail) : {};
      const cx = detail.clientX || 0;
      const cy = detail.clientY || 0;

      const marked = document.querySelectorAll('[data-rt-click-target="1"]');
      const visited = new Set();

      marked.forEach((el) => {
        el.removeAttribute("data-rt-click-target");
        let curr = el;
        for (let i = 0; i < 5 && curr && curr !== document.body; i++) {
          if (!visited.has(curr)) {
            visited.add(curr);
            fireReactClickOnNode(curr, cx, cy);
          }
          curr = curr.parentElement;
        }
      });
    } catch (e) {}
  });
})();
