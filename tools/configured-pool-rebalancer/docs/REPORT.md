# Báo Cáo Module Configured Pool Rebalancer

Tài liệu này giải thích module `configured_pool_rebalancer` cho người mới: hệ thống dùng để kiểm tra các vị thế V3 LP đã cấu hình, phát hiện vị thế đã ra ngoài range, sau đó lập kế hoạch hoặc thực thi rebalance.

## 1. Mục Đích Hệ Thống

`configured_pool_rebalancer` là một worker độc lập cho các pool và wallet được khai báo sẵn trong file JSON config.

Hệ thống không tự tìm pool mới và không copy range của competitor. Nó chỉ làm việc với pool, wallet, tokenId và chiến lược range đã được cấu hình.

Mặc định worker chạy ở chế độ `dry-run`: chỉ đọc dữ liệu on-chain, tính plan và in kết quả. Chỉ khi chạy với `--execute`, worker mới gửi transaction thật.

## 2. Luồng Hoạt Động Tổng Quan

```text
CLI
  -> load_worker_config()
  -> ConfiguredPoolRebalancer.run_once()
  -> Với mỗi pool:
      -> Kết nối RPC
      -> Tạo TxExecutor
      -> Tạo DexAdapter
      -> Tự bổ sung metadata của pool nếu config còn thiếu
      -> Kiểm tra swap pending từ lần chạy trước
      -> Refresh danh sách NFT position đang stake
      -> Đọc slot0/current tick
      -> Với mỗi position:
          -> Nếu còn trong range: IN_RANGE
          -> Nếu out-of-range:
              -> Build rebalance plan
              -> Dry-run: chỉ in plan
              -> Live-run:
                  -> Withdraw / collect liquidity cũ
                  -> Tính lượng token thu hồi
                  -> Swap nếu cần cân bằng token
                  -> Mint NFT position mới
                  -> Stake NFT mới
                  -> Burn NFT cũ nếu rỗng và được phép
                  -> Ghi journal, PnL, Discord nếu được cấu hình
```

Nói ngắn gọn: CLI đọc config, worker quét từng pool, position index tìm NFT đang stake, planner tính range mới, adapter tương tác contract, tx executor gửi giao dịch, journal ghi trạng thái, PnL reporter tạo báo cáo lời lỗ.

## 3. Các Thành Phần Chính

| File | Vai trò |
| --- | --- |
| `cli.py` | Entry point. Parse CLI flags, load config, chạy rebalance hoặc PnL report. |
| `settings.py` | Đọc JSON config, validate các giá trị quan trọng, convert address sang checksum. |
| `models.py` | Định nghĩa data model: pool config, worker config, position snapshot, plan, transaction result, status. |
| `worker.py` | Điều phối toàn bộ luồng rebalance cho từng pool và từng position. |
| `position_index.py` | Tìm tokenId đang stake bằng cache, legacy cache, seed token ids và log MasterChef. |
| `planner.py` | Tính range mới, amount cần mint và hướng swap nếu tỷ lệ token chưa cân bằng. |
| `adapter.py` | Lớp kết nối contract. Hiện tại live integration chính là PancakeSwap V3 MasterChef. |
| `tx_executor.py` | Build, sign, send transaction, quản lý nonce và gas policy. |
| `journal.py` | Tạo/cập nhật bảng `configured_rebalance_jobs`, ghi status và tx hash. |
| `pnl_report.py` | Tạo báo cáo PnL từ DB và receipt on-chain, xuất JSON/CSV. |
| `discord_notifier.py` | Gửi thông báo PnL sang Discord khi được bật. |
| `reward.py` | Lấy reward token và giá token USD từ PancakeSwap, DexScreener, CoinGecko. |
| `v3_math.py` | Hàm toán học V3: tick, range, liquidity, amount. |

## 4. Trạng Thái Position/Job

| Status | Ý nghĩa |
| --- | --- |
| `IN_RANGE` | Position vẫn nằm trong range hiện tại, không cần rebalance. |
| `PLANNED` | Position out-of-range và đã tạo kế hoạch range mới. |
| `WITHDRAWN_UNBURNED` | Đã decrease liquidity / collect / withdraw NFT cũ, nhưng NFT cũ chưa burn hoặc quy trình chưa xong. |
| `SWAP_PENDING` | Swap đã gửi nhưng worker chưa nhận receipt trong timeout. Chu kỳ sau sẽ kiểm tra lại. |
| `SWAP_BLOCKED` | Swap không thực hiện được vì không có quote, price impact cao, tx dropped hoặc reverted. |
| `MINTED_UNSTAKED` | Đã mint NFT mới nhưng chưa stake thành công hoặc chưa stake. |
| `REMINTED` | Đã mint NFT mới và stake thành công. |
| `BURNED` | NFT cũ đã rỗng và burn thành công. |
| `FAILED` | Quy trình gặp lỗi không thể tiếp tục an toàn. |

`OUT_OF_RANGE` có trong enum, nhưng luồng hiện tại thường trả về `PLANNED` khi phát hiện position out-of-range.

## 5. Cấu Hình Cần Nắm

Config mẫu nằm ở `latest_farms/configured_pool_rebalancer/sample_config.json`.

Config hiện hỗ trợ 2 dạng:

- Dạng cũ: mỗi pool tự khai báo đầy đủ `bot_wallet`, `managed_wallets`, slippage, burn policy. Field `private_key_env` cũ nếu còn trong config sẽ được bỏ qua.
- Dạng v2: khai báo `wallets` và `pool_defaults` một lần, sau đó mỗi pool chỉ cần các field riêng như `name`, `chain`, `pool_address`, `pid`.

Ví dụ dạng v2:

```json
{
  "version": 2,
  "wallets": {
    "main": {
      "bot_wallet": "0x...",
      "private_key_prefix_env": "CONFIGURED_REBALANCER_MAIN_PRIVATE_KEY_PREFIX"
    }
  },
  "pool_defaults": {
    "dex_type": "pancake_v3_masterchef",
    "wallet": "main",
    "slippage_bps": 10,
    "max_swap_price_impact_pct": 0.5,
    "execute_burn": false
  },
  "pools": [
    {
      "name": "USDT-GENIUS",
      "chain": "BNB",
      "pool_address": "0x...",
      "pid": 554
    }
  ]
}
```

Trong v2, loader sẽ merge `pool_defaults` vào từng pool, sau đó resolve `wallet` alias từ `wallets`. Nếu không khai báo `managed_wallets`, hệ thống tự dùng `[bot_wallet]`, phù hợp với setup một signer quản lý chính ví đó.

### Worker-Level Config

- `dry_run`: nên để `true` khi test. CLI `--execute` sẽ override thành live-run.
- `interval_seconds`: chu kỳ chạy khi dùng CLI `--loop`; mặc định 1800 giây / 30 phút.
- `cache_dir`: nơi lưu position index cache của module.
- `legacy_position_cache_dir`: nơi đọc legacy cache `positions_cache_{CHAIN}.json`.
- `use_legacy_position_cache`: nên bật để bootstrap nhanh tokenId lần đầu.
- `lock_timeout_seconds`: timeout khi lấy MySQL advisory lock.
- `pnl.native_prices_usd`: giá native token fallback nếu API giá bị lỗi.

### Local Bootstrap Không Cần `positions_cache`

Trên máy local, nên cấu hình rõ nguồn bootstrap thay vì phụ thuộc `latest_farms/positions_cache`.

Có 3 cách để hệ thống tìm position:

- `seed_token_ids`: điền tokenId NFT đã biết, phù hợp test nhanh/canary.
- `bootstrap_start_block`: lần đầu sweep log MasterChef `Deposit`/`Withdraw` từ block này tới latest, sau đó lưu cache riêng và các lần sau sync incremental.
- `auto_bootstrap_start_block`: mặc định bật; nếu không có block thủ công, worker tự tìm block `PoolCreated` từ V3 Factory rồi dùng block đó để sweep lần đầu.
- Legacy cache: copy `positions_cache_{CHAIN}.json` từ server và giữ `use_legacy_position_cache=true`.

Setup local độc lập nên dùng:

```json
{
  "use_legacy_position_cache": false,
  "pools": [
    {
      "name": "USDT-GENIUS",
      "chain": "BNB",
      "pool_address": "0x...",
      "pid": 554,
      "bootstrap_start_block": null,
      "auto_bootstrap_start_block": true
    }
  ]
}
```

Nếu không có cache module, không có legacy cache, không có `seed_token_ids`, không có block thủ công, và auto lookup block tạo pool thất bại, lần chạy đầu sẽ bỏ qua lịch sử cũ và có thể không thấy position đã stake trước đó.

### Pool-Level Config

- `name`: tên dễ đọc trong log/output.
- `chain`: chain key, ví dụ `BNB`, `BAS`.
- `dex_type`: hiện tại live integration chính là `pancake_v3_masterchef`.
- `pool_address`: địa chỉ V3 pool.
- `bot_wallet`: wallet ký transaction.
- `managed_wallets`: các wallet được phép quản lý. Position chỉ được xử lý nếu owner nằm trong danh sách này.
- `private_key_env`: field cũ chỉ còn để tương thích config và tiếp tục bị bỏ qua.
- `private_key_prefix_env`: tên biến môi trường chứa prefix từ 0 đến 63 ký tự hex của private key.
- `pid`: pool id trong MasterChef nếu có.
- `start_block`: block bắt đầu sweep log khi không có cache.
- `bootstrap_start_block`: block bootstrap local rõ ràng; dùng khi không muốn phụ thuộc legacy `positions_cache`.
- `auto_bootstrap_start_block`: tự tìm block tạo pool từ V3 Factory khi `bootstrap_start_block` chưa được set.
- `seed_token_ids`: danh sách tokenId thêm tay cho canary run hoặc khi cache thiếu.
- `slippage_bps`: slippage khi withdraw/mint/swap.
- `max_gas_gwei`: trần gas nếu không có policy riêng.
- `max_jobs_per_cycle`: số job live-run tối đa cho mỗi pool trong một lần chạy.
- `execute_burn`: có burn NFT cũ nếu nó rỗng và thuộc `bot_wallet` hay không.

### Range Strategy

Nếu khai báo:

```json
{
  "rebalance_range": {
    "mode": "price_percent",
    "lower_percent": -9.0,
    "upper_percent": 20.0
  }
}
```

Worker sẽ mint range mới quanh giá hiện tại, xấp xỉ từ -9% đến +20%.

Nếu không khai báo `rebalance_range`, worker sẽ thử suy ra percent range từ journal hoặc snapshot đầu tiên trong `wallet_nft_position`. Nếu không suy ra được, worker fallback về cách giữ width cũ và căn giữa quanh current tick.

### Gas Policy

Có thể cấu hình theo chain:

```json
{
  "gas_policy": {
    "BNB": {
      "mode": "fixed",
      "gas_price_gwei": 0.05,
      "max_fee_gwei": 0.08
    },
    "BAS": {
      "mode": "eip1559",
      "base_fee_multiplier": 2.0,
      "priority_fee_cap_gwei": 0.01,
      "swap_priority_fee_cap_gwei": 0.02,
      "swap_priority_fee_floor_gwei": 0.005,
      "max_fee_gwei": 0.1
    }
  }
}
```

Nếu gas tính ra vượt `max_fee_gwei` hoặc `max_gas_gwei`, transaction sẽ bị chặn để tránh trả gas quá cao.

### Discord

Discord mặc định tắt. Nếu bật, chỉ lưu tên biến môi trường trong config:

```json
{
  "discord": {
    "enabled": true,
    "webhook_url_env": "CONFIGURED_REBALANCER_DISCORD_WEBHOOK",
    "pnl_delay_seconds": 90,
    "notify_pending_if_snapshot_missing": false
  }
}
```

Set webhook bằng biến môi trường trước khi chạy. Không commit webhook URL thật vào repo.

```powershell
$env:CONFIGURED_REBALANCER_DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."
```

## 6. Hướng Dẫn Chạy

Chạy từ root repo:

```powershell
cd D:\python\nft_projects
```

### Dry-Run

Dùng để xem hệ thống sẽ làm gì, không gửi transaction:

```powershell
python -m latest_farms.configured_pool_rebalancer.cli --config path\to\config.json
```

Output là JSON. Nếu position trong range, bạn sẽ thấy `IN_RANGE`. Nếu out-of-range, bạn sẽ thấy `PLANNED`, `old_range`, `new_range`, `range_mode`, `lower_percent`, `upper_percent`.

### Live-Run

Live-run đọc prefix từ biến `private_key_prefix_env`, tự tính suffix còn lại để tổng bằng 64 ký tự, rồi yêu cầu nhập ẩn suffix cho từng `bot_wallet`. Key hoàn chỉnh được verify với `bot_wallet` và chỉ được giữ trong RAM của Python process khi tool đang chạy.

```powershell
python -m latest_farms.configured_pool_rebalancer.cli --config path\to\config.json --migrate --execute
```

Chạy live interactive loop theo `interval_seconds`:

```powershell
python -m latest_farms.configured_pool_rebalancer.cli --config path\to\config.json --migrate --execute --loop
```

Task Scheduler chỉ nên dùng cho dry-run/report. Live execute cần terminal interactive để operator nhập suffix còn lại của private key.

`--migrate` tạo/cập nhật bảng journal `configured_rebalance_jobs`. Nên chạy kèm live-run lần đầu hoặc sau khi code có thêm column mới.

### PnL Report

Lệnh này chỉ tạo báo cáo, không rebalance và không gửi transaction:

```powershell
python -m latest_farms.configured_pool_rebalancer.cli --config path\to\config.json --pnl-report
```

Mặc định ghi:

- `latest_farms/logs/configured_rebalancer_pnl.json`
- `latest_farms/logs/configured_rebalancer_pnl.csv`

Có thể đổi thư mục và format:

```powershell
python -m latest_farms.configured_pool_rebalancer.cli --config path\to\config.json --pnl-report --pnl-output-dir latest_farms\logs --pnl-format csv
```

### Scheduled Script

Repo có script `run_configured_rebalancer.ps1` để chạy worker và append log vào `latest_farms/logs/configured_rebalancer.log`.

Khi tạo script scheduler riêng:

- Set working directory về root repo.
- Set `PYTHONIOENCODING=utf-8`.
- Đặt prefix từ 0 đến 63 ký tự trong biến `private_key_prefix_env`; suffix còn lại sẽ nhập bằng hidden prompt khi live-run.
- Gọi `python -m latest_farms.configured_pool_rebalancer.cli --config my_rebalance_config.json --migrate --execute`.
- Không hard-code secret vào file được commit.

## 7. CLI Flags

| Flag | Ý nghĩa |
| --- | --- |
| `--config` | Đường dẫn file JSON config. Nếu không truyền, dùng env `CONFIGURED_REBALANCER_CONFIG` hoặc `sample_config.json`. |
| `--execute` | Gửi transaction thật. Không có flag này thì là dry-run. |
| `--loop` | Chạy liên tục theo `interval_seconds`; live mode prompt suffix còn lại một lần và giữ signer trong RAM. |
| `--migrate` | Tạo/cập nhật bảng journal trước khi chạy rebalance. |
| `--pnl-report` | Chỉ tạo PnL report, không rebalance, không migrate. |
| `--pnl-output-dir` | Thư mục ghi report. Mặc định `latest_farms/logs`. |
| `--pnl-format` | `json`, `csv`, hoặc `both`. Mặc định `both`. |
| `--log-level` | Mức logging, ví dụ `INFO`, `DEBUG`, `WARNING`. |

## 8. Cách Đọc Output Và Log

Ví dụ position đang trong range:

```json
[
  {
    "pool": "USDT-GENIUS",
    "state": "IN_RANGE",
    "tick": -7976,
    "token_id": 6848448
  }
]
```

Ví dụ dry-run hoặc plan live-run:

```json
[
  {
    "pool": "USDT-GENIUS",
    "token_id": 6846167,
    "state": "PLANNED",
    "old_range": [-7850, -6500],
    "new_range": [-8850, -7450],
    "range_mode": "price_percent",
    "dry_run": true
  }
]
```

Log `mysql.connector package`, `plugin_name`, `AUTHENTICATION_PLUGIN_CLASS` là log từ MySQL connector, không nhất thiết là lỗi nếu worker vẫn trả JSON thành công.

## 9. Lỗi Thường Gặp Và Cách Xử Lý

| Lỗi / hiện tượng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| `No working RPC for chain=...` | RPC trong config chung không kết nối được. | Kiểm tra `RPC_URLS_2`, `RPC_BACKUP_LIST`, network và chain key. |
| `gas too high` | Gas hiện tại vượt cap. | Tăng cap nếu chấp nhận chi phí, hoặc đợi lúc gas thấp hơn. |
| `runtime signer is required ...` | Live-run được gọi mà không có signer runtime. | Chạy qua CLI với `--execute`, cấu hình prefix và nhập suffix còn lại. |
| `private key does not match bot_wallet ...` | Prefix và suffix ghép lại không khớp `bot_wallet`. | Kiểm tra đúng prefix env và suffix trước khi chạy live. |
| `signer mismatch` | Owner của position không trùng `bot_wallet`. | Tạo pool config riêng cho wallet owner và dùng đúng ba segment của signer khi live-run. |
| `SWAP_PENDING` | Swap đã gửi nhưng timeout khi chờ receipt. | Chạy chu kỳ sau để worker recover; kiểm tra tx hash trên explorer nếu cần. |
| `SWAP_BLOCKED` | Không có route, price impact quá cao, tx dropped/reverted. | Kiểm tra thanh khoản, slippage, `max_swap_price_impact_pct`, amount dust. |
| `missing latest wallet_nft_position snapshot` | Indexer DB chưa bắt kịp NFT mới. | Đợi chu kỳ cập nhật snapshot rồi chạy lại PnL report. |
| PnL gas USD bằng `null` | Không lấy được giá native token. | Thêm fallback trong `pnl.native_prices_usd` hoặc kiểm tra API giá. |

## 10. Checklist Cho Người Mới Vận Hành

1. Copy `sample_config.json` thành file config riêng.
2. Điền wallet một lần trong `wallets`, ví dụ alias `main`.
3. Điền các setting dùng chung trong `pool_defaults`.
4. Với mỗi pool, chỉ điền `name`, `chain`, `pool_address`, `pid` và override thêm khi thật sự khác default.
5. Đặt `dry_run=true` trong config khi test.
6. Nếu dùng live-run, đặt prefix từ 0 đến 63 ký tự trong `.env` và nhập suffix còn lại qua hidden prompt; không lưu full private key.
7. Chạy dry-run và đọc `IN_RANGE` / `PLANNED`.
8. Nếu plan đúng, chạy live-run một lần với `--migrate --execute`, hoặc chạy interactive loop với `--migrate --execute --loop`.
9. Kiểm tra `configured_rebalance_jobs` và log sau khi chạy.
10. Chạy `--pnl-report` sau khi DB đã có snapshot NFT mới.
11. Nếu bật Discord, set webhook qua biến môi trường và không commit secret.

## 11. Giới Hạn Hiện Tại

- Live adapter hiện tại tập trung cho PancakeSwap V3 MasterChef.
- `aerodrome_gauge` đã có enum/adapter placeholder nhưng chưa implement.
- Worker chạy `run_once`; scheduler bên ngoài chịu trách nhiệm lập lịch.
- PnL phụ thuộc vào các bảng DB `wallet_nft_position`, `wallet_nft_summary`, `configured_rebalance_jobs` và khả năng fetch receipt/gia token.
