# 同花顺(10jqka.com.cn) 完整数据模型架构

## 1. K线数据模型 - d.10jqka.com.cn/v6/line/

### 1.1 URL 结构
```
https://d.10jqka.com.cn/v6/line/{prefix}_{code}/{period}/{file}.js
```

### 1.2 代码前缀体系

| 前缀 | 含义 | 适用范围 |
|------|------|----------|
| `hs_` | 沪深市场(统一前缀) | 所有A股+指数，**包括上海、深圳、创业板、科创板** |
| `sz_` | 深圳市场(旧版) | 仅000/002/300开头的深圳股票，**与hs_返回相同数据** |
| `zs_` | 指数(旧版) | **已废弃，v6版本返回空数据** |

**关键发现**: v6版本统一使用 `hs_` 前缀，不再区分市场。`sz_` 仍可用但等价于 `hs_`。

### 1.3 股票代码体系

| 代码前缀 | 市场 | marketType | 示例 |
|----------|------|------------|------|
| 000xxx | 深圳主板 | HS_stock_sz | hs_000001 (平安银行) |
| 002xxx | 中小板 | HS_stock_sz | hs_002001 (新和成) |
| 300xxx | 创业板 | HS_stock_gem | hs_300001 (特锐德) |
| 600/601/603/605xxx | 上海主板 | HS_stock_sh | hs_600519 (贵州茅台) |
| 688xxx | 科创板 | HS_stock_kcb | hs_688001 (华兴源创) |
| 689xxx | 科创板(存托) | HS_stock_kcb | (未测试) |
| 1A0001 | 上证指数 | (空) | hs_1A0001 |
| 1A0002 | A股指数 | (空) | hs_1A0002 |
| 1A0003 | B股指数 | (空) | hs_1A0003 |
| 399001 | 深证成指 | HS_stock_sz | hs_399001 |
| 399005 | 中小100 | HS_stock_sz | hs_399005 |
| 399006 | 创业板指 | HS_stock_sz | hs_399006 |
| 399300 | 沪深300 | HS_stock_sz | hs_399300 |
| 399673 | 创业板50 | HS_stock_sz | hs_399673 |
| 399905 | 中证500 | HS_stock_sz | hs_399905 |
| 830xxx | 北交所 | **不可用** | 所有前缀均返回空 |

### 1.4 周期代码映射

**代码结构**: `{周期}{复权方式}`

#### 周期前缀 (第一部分)
| 代码 | 周期 | 说明 |
|------|------|------|
| 0 | 日线 | 每日一根K线 |
| 1 | 周线 | 每周一根K线 |
| 2 | 月线 | 每月一根K线 |
| 3 | 5分钟 | 盘中5分钟K线 (仅近3年) |
| 4 | 15分钟 | 盘中15分钟K线 (仅近3年) |
| 5 | 30分钟 | 盘中30分钟K线 (仅近3年) |
| 6 | 60分钟 | 盘中60分钟K线 (仅近1年) |
| 7 | 日线(含时间) | 日线但含具体收盘时间 |
| 8 | 年线 | 每年一根K线 |
| 9 | 季线 | 每季一根K线 |

#### 复权后缀 (第二部分)
| 后缀 | 复权方式 | 说明 |
|------|----------|------|
| 0 | 不复权 | 原始价格，不做任何调整 |
| 1 | 前复权 | 最新价不变，历史价格向下调整 |
| 2 | 后复权 | 最早价不变，最新价格向上调整 |

#### 完整代码对照表
| 代码 | 含义 | 茅台总条数 | 起始日期 |
|------|------|-----------|----------|
| 01 | 日线-前复权 | 5953 | 20010827 |
| 02 | 日线-后复权 | 5953 | 20010827 |
| 00 | 日线-不复权 | 5953 | 20010827 |
| 11 | 周线-前复权 | 1253 | 20010831 |
| 12 | 周线-后复权 | 1253 | 20010831 |
| 21 | 月线-前复权 | 299 | 20010831 |
| 22 | 月线-后复权 | 299 | 20010831 |
| 31 | 5分钟-前复权 | 9888 | 20250825 |
| 41 | 15分钟-前复权 | 5888 | 20230619 |
| 51 | 30分钟-前复权 | 2944 | 20230619 |
| 61 | 60分钟-前复权 | 10122 | 20260506 |
| 71 | 日线(含时间)-前复权 | 736 | 20230619 |
| 81 | 年线-前复权 | 26 | 20011231 |
| 91 | 季线-前复权 | 101 | 20010928 |

### 1.5 文件类型

| 文件名 | 内容 | 格式 | 数据量 |
|--------|------|------|--------|
| `last.js` | 最近140根K线 | 明文，全字段 | 最小，约5-10KB |
| `{year}.js` | 指定年份所有K线 | 明文，全字段 | 中等，约5-20KB/年 |
| `all.js` | 全部历史数据 | 压缩，仅价格 | 最大，约190KB (5953条日线) |

### 1.6 K线完整字段定义

#### last.js / {year}.js 明文格式 (每行一根K线，分号分隔)
```
日期,开盘,最高,最低,收盘,成交量,成交额,换手率,(保留),融资买入额,融资买入量/标志位
```

**字段详解**:
| 序号 | 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|------|
| 1 | date | string | 日期(日线YYYYMMDD, 分钟线YYYYMMDDHHmm) | 20251203 |
| 2 | open | float | 开盘价 | 1383.83 |
| 3 | high | float | 最高价 | 1383.99 |
| 4 | low | float | 最低价 | 1376.02 |
| 5 | close | float | 收盘价 | 1377.12 |
| 6 | volume | int | 成交量(股) | 2357279 |
| 7 | amount | float | 成交额(元) | 3370080100.00 |
| 8 | turnover_rate | float | 换手率(%) | 0.188 |
| 9 | (reserved) | float | 保留字段，始终为空 | |
| 10 | margin_amount | float | 融资买入额(元)，仅融资融券标的股有值 | 2000.00 |
| 11 | margin_vol_or_flag | int | 融资买入量(股) 或 标志位 | 50540 / 0 |

**不同市场的字段差异**:
- **主板(600xxx/000xxx)**: 字段10和11均为空/0，字段11的0表示非ST
- **创业板(300xxx)**: 字段10=融资买入额，字段11=融资买入量
- **科创板(688xxx)**: 字段10=0.00, 字段11=0 (科创板有融券但字段为0)
- **指数(1Axxxx/399xxx)**: 字段10和11均为空/0

#### all.js 压缩格式
```
4个数值为一根K线: close_ref, open_delta, high_delta, low_delta
```

**解码规则**:
- `priceFactor` = 100 (价格放大倍数)
- 当 `close_ref < 0`: 绝对收盘价 = close_ref / priceFactor
- 当 `close_ref >= 0`: 收盘价 = 前一根收盘价 + close_ref / priceFactor
- 开盘价 = 收盘价 + open_delta / priceFactor
- 最高价 = 收盘价 + high_delta / priceFactor
- 最低价 = 收盘价 + low_delta / priceFactor

**注意**: all.js 仅包含价格数据，不含成交量/成交额/换手率/融资数据。

### 1.7 JSONP回调函数命名规则
```
quotebridge_v6_line_{prefix}_{code}_{period}_{file}
```
示例: `quotebridge_v6_line_hs_600519_01_last`

### 1.8 JSONP返回的元数据字段
| 字段 | 说明 | 示例 |
|------|------|------|
| rt | 交易时间段 | "0930-1130,1300-1500" |
| start | 最早日期 | "20010827" |
| total | 总K线数量 | "5953" |
| num | 本次返回数量 | 140 |
| name | 股票名称 | "贵州茅台" |
| year | 各年份K线条数 | {"2001":86, "2002":234, ...} |
| marketType | 市场类型 | "HS_stock_sh" |
| priceFactor | 价格因子(all.js) | 100 |
| sortYear | 排序后年份(all.js) | [[2001,86], ...] |

### 1.9 v2版本
`v2` 路径同样可用: `https://d.10jqka.com.cn/v2/line/hs_600519/01/last.js`
数据格式与v6完全相同，仅回调函数前缀为 `quotebridge_v2_line_`。

---

## 2. 涨停数据模型 - data.10jqka.com.cn/dataapi/

### 2.1 已确认的API端点

| 端点 | 说明 | 状态 |
|------|------|------|
| `limit_up/limit_up_pool` | 涨停股票池 | 可用 |
| `limit_up/continuous_limit_up` | 连板股 | 可用 |

**注意**: 404的端点: limit_up_count, limit_down/*, broken_pool, again_pool, open_pool, strong_pool, hot_pool, concept_pool, industry_pool, emotion_pool, preview_pool

### 2.2 limit_up_pool 请求参数

```
GET https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool
```

| 参数 | 必填 | 说明 | 示例 |
|------|------|------|------|
| page | 是 | 页码 | 1 |
| limit | 是 | 每页数量 | 10 |
| field | 是 | 字段代码，逗号分隔 | 199112,10,9001,... |
| filter | 是 | 市场筛选，**仅支持HS** | HS |
| date | 否 | 日期YYYYMMDD，不填则返回最新交易日 | 20250630 |
| start_date | 否 | 开始日期(范围查询) | 20250601 |
| end_date | 否 | 结束日期(范围查询) | 20250630 |

### 2.3 field参数数字代码映射

通过逐一测试每个field参数，确认以下映射：

| 代码 | 返回字段 | 说明 |
|------|----------|------|
| 199112 | change_rate | 涨幅(%) |
| 10 | latest | 最新价 |
| 9001 | reason_type | 涨停原因/概念标签 |
| 330323 | first_limit_up_time | 首次涨停时间(Unix时间戳) |
| 330324 | last_limit_up_time | 最后涨停时间(Unix时间戳) |
| 330325 | limit_up_type | 涨停类型(换手板/一字板) |
| 9002 | open_num | 开板次数 |
| 330329 | high_days | 连板天数(文本，如"4天3板"/"首板") |
| 133971 | order_volume | 封单量(股) |
| 133970 | order_amount | 封单额(元) |
| 1968584 | turnover_rate | 换手率(%) |
| 3475914 | currency_value | 流通市值(元) |
| 9003 | limit_up_suc_rate | 涨停成功率(0-1) |
| 9004 | time_preview | 分时涨幅预览(80个值，3分钟间隔) |

**始终返回的字段** (无需在field中指定):
| 字段 | 说明 |
|------|------|
| code | 股票代码 |
| name | 股票名称 |
| market_id | 市场ID (17=上海, 33=深圳) |
| market_type | 市场类型 (HS) |
| is_new | 是否新股 (0/1) |
| is_again_limit | 是否再涨停 (0/1) |
| change_tag | 涨停标签 (FIRST_LIMIT/LIMIT_BACK) |
| high_days_value | 连板天数编码值 |

### 2.4 high_days_value 编码规则

采用位编码: `hex = 0x{天数的hex}{连板数的hex}`

```
high_days_value = (天数 << 16) | 连板数
```

| 示例值 | 十六进制 | 天数 | 连板数 | 含义 |
|--------|----------|------|--------|------|
| 65537 | 0x10001 | 1 | 1 | 首板(1天1板) |
| 131073 | 0x20001 | 2 | 1 | 2天1板(次日涨停开板) |
| 196612 | 0x30004 | 3 | 4 | 4天3板(错误?应为3天3板) |
| 327685 | 0x50005 | 5 | 5 | 5天5板 |
| 262147 | 0x40003 | 4 | 3 | 4天3板 |

**解码公式**:
```python
days = (high_days_value >> 16) & 0xFFFF
height = high_days_value & 0xFFFF
# 结果: "{days}天{height}板"
```

### 2.5 time_preview 字段

- 长度: 80个浮点值
- 含义: 分时涨幅预览(从开盘到当前/收盘)
- 间隔: 3分钟/点 (240分钟 / 3 = 80个点)
- 覆盖: 9:30-11:30, 13:00-15:00

### 2.6 market_id 含义

| market_id | 含义 | 对应代码 |
|-----------|------|----------|
| 17 | 上海证券交易所 | 600/601/603/605/688开头 |
| 33 | 深圳证券交易所 | 000/002/300开头 |

### 2.7 change_tag 枚举

| 值 | 含义 |
|----|------|
| FIRST_LIMIT | 首次涨停(首板) |
| LIMIT_BACK | 再涨停(连板) |

### 2.8 limit_up_pool 完整响应结构

```json
{
  "status_code": 0,
  "data": {
    "page": {
      "limit": 10,
      "total": 65,
      "count": 33,
      "page": 1
    },
    "info": [
      {
        "open_num": 8,
        "first_limit_up_time": "1751248617",
        "last_limit_up_time": "1751249277",
        "code": "002189",
        "limit_up_type": "换手板",
        "order_volume": 2597998.0,
        "is_new": 0,
        "limit_up_suc_rate": 0.8,
        "currency_value": 8043725600.0,
        "market_id": 33,
        "is_again_limit": 1,
        "change_rate": 10.0,
        "turnover_rate": 19.7026,
        "reason_type": "兵装重组+汽车光电+光学元组件+AI眼镜",
        "order_amount": 80018338.0,
        "high_days": "4天3板",
        "name": "中光学",
        "high_days_value": 196612,
        "change_tag": "LIMIT_BACK",
        "market_type": "HS",
        "latest": 30.8,
        "time_preview": [0.17, 1.64, 2.04, ...]  // 80个值
      }
    ],
    "limit_up_count": {
      "today": {
        "num": 65,          // 今日涨停数
        "history_num": 78,  // 曾涨停数(含炸板)
        "rate": 0.8333,     // 封板率
        "open_num": 13      // 炸板数
      },
      "yesterday": {
        "num": 43,
        "history_num": 67,
        "rate": 0.6418,
        "open_num": 24
      }
    },
    "limit_down_count": {
      "today": { "num": 3, "history_num": 4, "rate": 0.75, "open_num": 1 },
      "yesterday": { "num": 3, "history_num": 4, "rate": 0.75, "open_num": 1 }
    },
    "date": "20250630",
    "msg": null,
    "trade_status": {
      "id": "non_trading_day",     // trading_day / non_trading_day
      "name": "非交易日",
      "start_time": "00:00",
      "end_time": "23:59:59.999"
    }
  },
  "status_msg": "success"
}
```

### 2.9 continuous_limit_up (连板股) 响应结构

```json
{
  "status_code": 0,
  "data": [
    {
      "height": 5,           // 连板高度
      "code_list": [
        {
          "code": "001236",
          "name": "弘业期货",
          "market_id": 33,
          "continue_num": 5   // 连板数
        }
      ],
      "number": 2            // 该高度股票数
    },
    {
      "height": 4,
      "code_list": [...],
      "number": 1
    }
  ],
  "status_msg": "success"
}
```

---

## 3. 隐藏API与发现

### 3.1 已确认可用的API

| API | URL | 说明 |
|-----|-----|------|
| K线数据 | `d.10jqka.com.cn/v6/line/` | 无反爬，无需UA限制 |
| 涨停池 | `data.10jqka.com.cn/dataapi/limit_up/limit_up_pool` | 需要浏览器UA |
| 连板股 | `data.10jqka.com.cn/dataapi/limit_up/continuous_limit_up` | 需要浏览器UA |
| 券商配置 | `basic.10jqka.com.cn/basicapi/broker/config/` | 已404 |

### 3.2 已确认不可用的端点 (404)

```
dataapi/limit_up/limit_up_count
dataapi/limit_down/* (所有)
dataapi/limit_up/broken_pool
dataapi/limit_up/again_pool
dataapi/limit_up/open_pool
dataapi/limit_up/strong_pool
dataapi/limit_up/hot_pool
dataapi/limit_up/concept_pool
dataapi/limit_up/industry_pool
dataapi/limit_up/emotion_pool
dataapi/limit_up/preview_pool
dataapi/dragon_tiger/pool
dataapi/big_deal/pool
dataapi/margin/pool
```

### 3.3 dataapi 请求要点

- **filter参数**: 仅支持 `HS`，不支持 SH/SZ/BJ/KC/CY/ZXB/SHA/SZA/HS_A
- **date参数**: 可不填，默认返回最新交易日数据
- **支持日期范围**: `start_date` + `end_date` 参数
- **必须Header**: `User-Agent` 不能是 python-requests，需用浏览器UA
- **推荐Header**: `Referer: https://data.10jqka.com.cn/`

### 3.4 旧版API (JS中发现)

```
d.10jqka.com.cn/quote.php?cate=real&type=stock&return=json&callback=showStockData&code=600519
```
此端点已返回 "File not found"，可能已迁移。

---

## 4. 股票代码体系完整参考

### 4.1 market_id 与 market_type

| market_id | market_type | 含义 |
|-----------|-------------|------|
| 17 | HS_stock_sh | 上海证券交易所 |
| 33 | HS_stock_sz | 深圳证券交易所 |
| (未知) | HS_stock_gem | 创业板(33下的子类) |
| (未知) | HS_stock_kcb | 科创板(17下的子类) |

**注意**: market_type 在 K线API 和 dataapi 中的表达不同:
- K线 all.js 中 `marketType`: `HS_stock_sh` / `HS_stock_sz` / `HS_stock_gem` / `HS_stock_kcb`
- dataapi 中 `market_type`: 统一为 `HS`，通过 `market_id` 区分沪深

### 4.2 6位代码规则

| 代码范围 | 市场 | 板块 | 示例 |
|----------|------|------|------|
| 000001-002999 | 深圳 | 主板+中小板 | 000001平安银行, 002001新和成 |
| 300001-301999 | 深圳 | 创业板 | 300001特锐德 |
| 600000-685999 | 上海 | 主板 | 600519贵州茅台, 601398工商银行 |
| 688000-689999 | 上海 | 科创板 | 688001华兴源创 |
| 830000-871999 | 北京 | 北交所 | (K线API不支持) |

### 4.3 指数代码规则

| 代码 | 名称 | 类型 |
|------|------|------|
| 1A0001 | 上证指数 | 上海综合 |
| 1A0002 | A股指数 | 上海A股 |
| 1A0003 | B股指数 | 上海B股 |
| 399001 | 深证成指 | 深圳成分 |
| 399005 | 中小100 | 中小板指数 |
| 399006 | 创业板指 | 创业板指数 |
| 399300 | 沪深300 | 跨市场 |
| 399673 | 创业板50 | 创业板龙头 |
| 399905 | 中证500 | 跨市场 |

---

## 5. 请求配置要点

### 5.1 K线API (d.10jqka.com.cn)
- **无反爬机制**: 无需特殊Header，无频率限制
- **推荐Header**: `User-Agent: Mozilla/5.0`, `Referer: https://stockpage.10jqka.com.cn/`
- **数据格式**: JSONP回调，需提取括号内JSON
- **字符编码**: UTF-8，中文名使用 \uXXXX 转义

### 5.2 Dataapi (data.10jqka.com.cn)
- **需要浏览器UA**: 使用 python-requests 的UA会返回404
- **必须Header**: `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36`
- **推荐Header**: `Referer: https://data.10jqka.com.cn/`
- **数据格式**: 纯JSON，无JSONP包装
- **字符编码**: UTF-8，但中文可能乱码(GBK编码问题)
- **无需Cookie/Token**: 不需要登录或验证码

### 5.3 前端架构发现
- stockpage 已迁移到 Next.js 架构 (`news-p-fe-app-news-flow-home/market/`)
- data.10jqka.com.cn 主页仍用旧版 jQuery 架构
- K线图表由独立的 Flash/JS 组件渲染，通过 JSONP 加载数据
- dataapi 前端JS未找到在CDN公开文件中，可能是内嵌/动态加载
