"""Mainnet $10 真单验证 — SHORT 0.003 ETH maker 单 + 平仓"""
import asyncio
import os
import httpx
import hmac
import hashlib
import time
from dotenv import load_dotenv
load_dotenv('.env')


async def signed(http, base, key, secret, method, path, params=None):
    ts = str(int(time.time() * 1000))
    params = params or {}
    params['timestamp'] = ts
    params['recvWindow'] = 60000
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    headers = {'X-MBX-APIKEY': key}
    url = f'{base}{path}?{qs}&signature={sig}'
    if method == 'GET':
        r = await http.get(url, headers=headers)
    elif method == 'POST':
        r = await http.post(url, headers=headers)
    elif method == 'DELETE':
        r = await http.delete(url, headers=headers)
    return r.status_code, r.json()


async def query_order(http, base, key, secret, symbol, order_id):
    return await signed(http, base, key, secret, 'GET', '/fapi/v1/order', {
        'symbol': symbol, 'orderId': order_id,
    })


async def get_position(http, base, key, secret, symbol):
    sc, r = await signed(http, base, key, secret, 'GET', '/fapi/v2/positionRisk', {'symbol': symbol})
    if sc == 200 and r:
        return r[0]
    return None


async def get_balance(http, base, key, secret):
    ts = str(int(time.time() * 1000))
    qs = f'timestamp={ts}&recvWindow=60000'
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    r = await http.get(f'{base}/fapi/v2/balance?{qs}&signature={sig}', headers={'X-MBX-APIKEY': key})
    if r.status_code == 200:
        for b in r.json():
            if b.get('asset') == 'USDT':
                return float(b.get('balance', 0)), float(b.get('availableBalance', 0))
    return None, None


async def main():
    key = os.environ['SELENE_EXCHANGE_API_KEY']
    secret = os.environ['SELENE_EXCHANGE_API_SECRET']
    proxy = os.environ.get('SELENE_PROXY_URL', '').strip() or None
    base = 'https://fapi.binance.com'
    symbol = 'ETHUSDT'

    # 初始余额
    async with httpx.AsyncClient(proxy=proxy, timeout=15.0) as http:
        bal0, avail0 = await get_balance(http, base, key, secret)
        print(f'[0] start balance: {bal0} available: {avail0}')

        # 1. 设杠杆 3x
        sc, r = await signed(http, base, key, secret, 'POST', '/fapi/v1/leverage', {
            'symbol': symbol, 'leverage': 3,
        })
        print(f'[1] set leverage 3x: {sc} {r if sc != 200 else "OK"}')

        # 2. 设 margin type ISOLATED
        sc, r = await signed(http, base, key, secret, 'POST', '/fapi/v1/marginType', {
            'symbol': symbol, 'marginType': 'ISOLATED',
        })
        print(f'[2] set margin ISOLATED: {sc} {r if sc != 200 else "OK"}')

        # 3. 查盘口
        r_book = await http.get(f'{base}/fapi/v1/ticker/bookTicker?symbol={symbol}')
        book = r_book.json()
        bid = float(book['bidPrice'])
        ask = float(book['askPrice'])
        print(f'[3] bid={bid} ask={ask}')

        # 4. 挂限价卖单（SHORT）0.003 ETH @ bid
        limit_price = bid
        sc, r = await signed(http, base, key, secret, 'POST', '/fapi/v1/order', {
            'symbol': symbol,
            'side': 'SELL',
            'type': 'LIMIT',
            'quantity': '0.011',
            'price': f'{limit_price:.2f}',
            'timeInForce': 'GTC',
        })
        print(f'[4] LIMIT SELL 0.011 @ {limit_price:.2f}: sc={sc}')
        if sc != 200:
            print(f'    ERROR: {r}')
            return
        order_id = r.get('orderId')
        print(f'    orderId={order_id} status={r.get("status")}')

        # 5. 轮询等成交（最多 5 分钟）
        print('[5] waiting for fill (5min max)...')
        start = time.time()
        filled = False
        entry = 0.0
        while time.time() - start < 300:
            await asyncio.sleep(10)
            sc, r = await query_order(http, base, key, secret, symbol, order_id)
            if sc != 200:
                print(f'    query failed: {r}')
                continue
            status = r.get('status')
            executed = r.get('executedQty', '0')
            print(f'    [{int(time.time()-start)}s] status={status} executed={executed}')
            if status == 'FILLED':
                filled = True
                entry = float(r.get('avgPrice', 0))
                print(f'    FILLED at avgPrice={entry}')
                break
            if status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                print(f'    order {status}, abort')
                return

        if not filled:
            sc, r = await signed(http, base, key, secret, 'DELETE', '/fapi/v1/order', {
                'symbol': symbol, 'orderId': order_id,
            })
            print(f'[6] 5min no fill, cancel: {sc}')
            return

        # 6. 查持仓
        p = await get_position(http, base, key, secret, symbol)
        if p:
            print(f'[6] position: amt={p.get("positionAmt")} entry={p.get("entryPrice")} liq={p.get("liquidationPrice")} margin={p.get("isolatedMargin")}')

        # 7. 挂限价买单（平仓）@ entry - 0.11
        close_price = entry - 0.40
        print(f'[7] target close price={close_price:.2f}')
        sc, r = await signed(http, base, key, secret, 'POST', '/fapi/v1/order', {
            'symbol': symbol,
            'side': 'BUY',
            'type': 'LIMIT',
            'quantity': '0.011',
            'price': f'{close_price:.2f}',
            'timeInForce': 'GTC',
            'reduceOnly': 'true',
        })
        if sc != 200:
            print(f'[7] LIMIT BUY ERROR: {r}')
            return
        close_order_id = r.get('orderId')
        print(f'[7] LIMIT BUY 0.003 @ {close_price:.2f}: orderId={close_order_id}')

        # 8. 轮询等平仓，监控硬止损 entry + 0.3‰
        hard_stop = entry * 1.0003
        print(f'[8] waiting close (5min max, hard stop @ {hard_stop:.2f})...')
        start = time.time()
        closed = False
        while time.time() - start < 300:
            await asyncio.sleep(10)
            r_ticker = await http.get(f'{base}/fapi/v1/ticker/price?symbol={symbol}')
            cur_price = float(r_ticker.json()['price'])

            p = await get_position(http, base, key, secret, symbol)
            if p and float(p.get('positionAmt', 0)) == 0:
                sc2, r2 = await query_order(http, base, key, secret, symbol, close_order_id)
                print(f'    [{int(time.time()-start)}s] CLOSED! price={cur_price} close_status={r2.get("status") if sc2==200 else "?"} avgPrice={r2.get("avgPrice") if sc2==200 else "?"}')
                closed = True
                break

            if cur_price >= hard_stop:
                print(f'    [{int(time.time()-start)}s] HARD STOP! price={cur_price} >= {hard_stop:.2f}')
                await signed(http, base, key, secret, 'DELETE', '/fapi/v1/order', {
                    'symbol': symbol, 'orderId': close_order_id,
                })
                sc_m, r_m = await signed(http, base, key, secret, 'POST', '/fapi/v1/order', {
                    'symbol': symbol, 'side': 'BUY',
                    'type': 'MARKET',
                    'quantity': '0.011', 'reduceOnly': 'true',
                })
                print(f'    market close: sc={sc_m} status={r_m.get("status") if sc_m==200 else r_m}')
                closed = True
                break

            print(f'    [{int(time.time()-start)}s] price={cur_price} position open')

        if not closed:
            print('[9] 5min timeout, market close')
            await signed(http, base, key, secret, 'DELETE', '/fapi/v1/order', {
                'symbol': symbol, 'orderId': close_order_id,
            })
            sc, r = await signed(http, base, key, secret, 'POST', '/fapi/v1/order', {
                'symbol': symbol, 'side': 'BUY',
                'type': 'MARKET',
                'quantity': '0.011', 'reduceOnly': 'true',
            })
            print(f'    market close: sc={sc}')

        # 10. 最终余额 + 持仓
        await asyncio.sleep(2)
        bal1, avail1 = await get_balance(http, base, key, secret)
        print(f'[10] FINAL balance: {bal1} available: {avail1}')
        pnl = (bal1 - bal0) if bal0 and bal1 else 0
        print(f'    PnL: {pnl:.4f} USDT ({(pnl/bal0*100):.3f}%)')


if __name__ == '__main__':
    asyncio.run(main())
