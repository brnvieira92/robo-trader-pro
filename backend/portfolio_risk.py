"""Shared gross SL risk. Opposing USD exposures never receive hedge credit."""
import math

ACTIVE = {'SUBMITTING', 'PENDING', 'OPEN', 'NEEDS_REVIEW'}


def position_limit(config):
    return config.get('forex_max_open_positions', 2) if config.get('forex_multi_enabled') else 1


def active_trades(trades):
    return [t for t in trades if t.get('mode') == 'LIVE_MT5' and t.get('status') in ACTIVE]


def reserved_risk(trade):
    value = float(trade.get('reserved_risk', trade.get('amount', trade.get('risk_budget', 0))))
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Reserva de risco da posição não confirmada.')
    return value


def global_budget(config, equity, remaining_loss):
    cap = max(0, float(equity)) * config['risk_per_trade_pct'] / 100
    if config['bankroll_mode'] != 'PERCENT':
        cap = min(cap, config['entry_amount'])
    return max(0, min(cap, remaining_loss))


def context(trades):
    keys = ('id','mode','status','account_login','account_server','symbol','action',
            'protected','position_id','amount','risk_budget','reserved_risk','sync_error','deal_time_warning',
            'stop_loss','take_profit','filled_volume','entry_price','cost_buffer','close_intent')
    return [{k:t[k] for k in keys if k in t} for t in active_trades(trades)]


def local_block(config, trades, symbol=None):
    active = active_trades(trades)
    if any((t.get('close_intent') or {}).get('status') in ('SUBMITTING','PENDING','PARTIAL_REVIEW') for t in active):
        return 'Fechamento em confirmação; novas entradas aguardam reconciliação global.'
    if any(t['status'] != 'OPEN' or not t.get('protected') or not t.get('position_id') or t.get('sync_error') or t.get('deal_time_warning') for t in active):
        return 'Exposição incerta ou proteção não confirmada; aguarde reconciliação global.'
    if len(active) >= position_limit(config):
        return 'Limite global de posições simultâneas atingido.'
    if symbol and any(t['symbol'] == symbol for t in active):
        return 'Já existe posição/intenção neste símbolo.'
    return None


def inspect_portfolio(config, trades, positions, orders, account, remaining_loss, calc_profit,
                      symbol=None, proposed_risk=0, require_slot=True):
    if positions is None or orders is None:
        raise ValueError('Não foi possível confirmar exposição global da conta.')
    if orders:
        raise ValueError('Há ordem pendente na conta; exposição global ainda incerta.')
    active = active_trades(trades)
    block = local_block(config, active, symbol)
    if block and not (not require_slot and block == 'Limite global de posições simultâneas atingido.'):
        raise ValueError(block)
    indexed = {int(t['position_id']):t for t in active}
    if len(indexed) != len(active):
        raise ValueError('Identificação duplicada de posições no diário.')
    found = set(); risks = {}; usd = {'LONG':0., 'SHORT':0.}
    mapping = {p['symbol']:p['pair'] for p in config.get('forex_pairs', [])}
    for p in positions:
        identifier = int(getattr(p, 'identifier', 0) or getattr(p, 'ticket', 0))
        t = indexed.get(identifier)
        if t is None or identifier in found or getattr(p, 'symbol', None) != t['symbol']:
            raise ValueError('Posição externa ou não correlacionada; exposição global bloqueada.')
        if t.get('account_login') != account['login'] or t.get('account_server') != account['server']:
            raise ValueError('Conta da posição difere da conta vinculada.')
        found.add(identifier)
        buy = t['action'] == 'BUY'
        sl = float(getattr(p, 'sl', 0)); tp = float(getattr(p, 'tp', 0))
        volume = float(getattr(p, 'volume', t.get('filled_volume', 0)))
        entry = float(getattr(p, 'price_open', t.get('entry_price', 0)))
        if (not math.isfinite(volume) or not math.isfinite(entry) or
            abs(volume-float(t.get('filled_volume', 0)))>1e-8 or
            not math.isclose(entry,float(t.get('entry_price',0)),rel_tol=1e-8,abs_tol=1e-8) or
            getattr(p,'type',0 if buy else 1)!=(0 if buy else 1)):
            raise ValueError('Volume/direção da posição mudou; aguarde reconciliação global.')
        loss = calc_profit(0 if buy else 1, t['symbol'], volume, entry, sl) if sl > 0 and volume > 0 and entry > 0 else None
        widened = sl < t['stop_loss']-1e-8 if buy else sl > t['stop_loss']+1e-8
        target_ok = tp > entry if buy else 0 < tp < entry
        if loss is None or not math.isfinite(float(loss)) or widened or not target_ok:
            raise ValueError('SL/TP ou risco da posição não confirmados; exposição global bloqueada.')
        risk = max(reserved_risk(t), max(0, -float(loss))+float(t.get('cost_buffer', 0)))
        if risk > t['risk_budget']+1e-8:
            raise ValueError('Risco da posição ultrapassa sua reserva autorizada.')
        risks[t['id']] = risk
        pair = mapping.get(t['symbol'], t['symbol'][:6])
        long_usd = (pair[:3] == 'USD' and buy) or (pair[3:6] == 'USD' and not buy)
        if 'USD' in (pair[:3], pair[3:6]):
            usd['LONG' if long_usd else 'SHORT'] += risk
    if found != set(indexed):
        raise ValueError('Posição do diário ausente no terminal; aguarde confirmação de fechamento.')
    limit = global_budget(config, account['equity'], remaining_loss)
    used = sum(risks.values())
    if not math.isfinite(proposed_risk) or proposed_risk < 0 or used+proposed_risk > limit+1e-8:
        raise ValueError('Orçamento global de risco insuficiente para a carteira.')
    return dict(max_open_positions=position_limit(config), open_positions=len(active),
                risk_limit=limit, reserved_risk=used, remaining_risk=max(0, limit-used),
                per_position_cap=limit/position_limit(config), usd_risk=usd,
                usd_risk_policy='Soma bruta até SL, sem compensar direções opostas.', risks=risks)
