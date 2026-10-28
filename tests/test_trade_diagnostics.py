import asyncio
import copy
import math
import pytest
from backend.trade_diagnostics import observe_exit_quote
from test_mt5_live import live_bot, signal


@pytest.mark.parametrize('side,quotes',[('BUY',[1.101,1.0995]),('SELL',[1.099,1.1005])])
def test_excursions_are_directional_and_survive_reload(side,quotes):
    trade=dict(action=side,entry_price=1.1,stop_loss=1.098 if side=='BUY' else 1.102)
    observe_exit_quote(trade,quotes[0],10,'TEST')
    restored=copy.deepcopy(trade)
    observe_exit_quote(restored,quotes[1],11,'TEST')
    assert restored['sampled_excursions']['mfe_r']==pytest.approx(.5)
    assert restored['sampled_excursions']['mae_r']==pytest.approx(.25)
    assert restored['sampled_excursions']['first_observed_at']==10
    assert restored['sampled_excursions']['samples']==2
    assert restored['stop_loss']==trade['stop_loss']
    snapshot=copy.deepcopy(restored)
    observe_exit_quote(restored,2,9,'OLD')
    assert restored==snapshot


@pytest.mark.parametrize('price',[None,0,-1,math.nan,math.inf,'invalid'])
def test_missing_invalid_quotes_never_block_execution(price):
    trade=dict(action='BUY',entry_price=1.1,stop_loss=1.098)
    before=copy.deepcopy(trade)
    observe_exit_quote(trade,price,10,'TEST')
    assert trade==before


def test_live_reconciliation_persists_diagnostics_without_extra_orders(live_bot):
    b,sdk=live_bot
    async def run():
        await b.execute_trade_from_signal(signal(),manual=True)
        sdk.positions[0].price_current=1.101
        await b.reconcile_live()
        trade=b.trade_history[0]
        assert trade['sampled_excursions']['mfe_price']>0
        first=copy.deepcopy(trade['sampled_excursions'])
        sdk.positions[0].price_current=1.0995
        await b.reconcile_live()
        assert trade['sampled_excursions']['mae_price']>0
        assert trade['sampled_excursions']['mfe_price']==first['mfe_price']
        assert b.journal.all_trades()[0]['sampled_excursions']==trade['sampled_excursions']
        sdk.close_position();await b.reconcile_live()
        assert trade['status']=='WIN'
        assert trade['sampled_excursions']['source']=='MT5_CONFIRMED_EXIT'
        assert trade['sampled_excursions']['samples']==3
        assert len(sdk.sent)==1
    asyncio.run(run())


def test_invalid_optional_position_quote_is_not_persisted_as_nan(live_bot):
    b,sdk=live_bot
    async def run():
        await b.execute_trade_from_signal(signal(),manual=True)
        sdk.positions[0].price_current=math.nan
        await b.reconcile_live()
        trade=b.trade_history[0]
        assert trade['status']=='OPEN' and trade['observed_exit_quote'] is None
        assert 'sampled_excursions' not in trade and not trade.get('sync_error')
    asyncio.run(run())
