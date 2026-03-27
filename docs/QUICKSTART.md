# MoleculerPy Channels — Quick Start Guide

**Goal**: Get up and running in 5 minutes.

---

## 1. Installation (2 minutes)

```bash
# Clone or navigate to project
cd sources/moleculerpy-channels

# Create virtual environment
python -m venv .venv

# Activate
source .venv/bin/activate  # macOS/Linux
# or
.venv\Scripts\activate     # Windows

# Install with all adapters
pip install -e ".[all,dev]"
```

**Verify**:
```bash
python -c "from moleculerpy_channels import ChannelsMiddleware; print('✅ Installed')"
```

---

## 2. Run Unit Tests (30 seconds)

```bash
# No external dependencies required
pytest tests/unit/ -v
```

**Expected**:
```
========================= 24 passed in 0.75s ==========================
```

---

## 3. Start Docker Services (1 minute)

```bash
# Start Redis + NATS
docker compose up -d

# Verify health
docker compose ps
```

**Expected**:
```
NAME                           STATUS          PORTS
moleculerpy-channels-redis       Up (healthy)    0.0.0.0:6379->6379/tcp
moleculerpy-channels-nats        Up (healthy)    0.0.0.0:4222->4222/tcp
```

---

## 4. Run Integration Tests (30 seconds)

```bash
# Redis + NATS integration tests
pytest tests/integration/ -v
```

**Expected**:
```
test_redis_basic.py   10 passed
test_nats_basic.py    10 passed
========================= 20 passed in 21.5s ==========================
```

---

## 5. Run Example (1 minute)

```bash
# Simple pub/sub example
python examples/simple.py
```

**Expected**:
```
[INFO] RedisAdapter: Connected
[INFO] Subscribed to 'orders.created'
[INFO] Publishing message...
[INFO] ✅ Received: {'orderId': 123, 'items': ['item1', 'item2']}
[INFO] Disconnecting...
```

---

## 6. Cleanup

```bash
# Stop Docker services
docker compose down

# Deactivate virtualenv
deactivate
```

---

## Next Steps

- **Full Testing Guide**: [`TESTING-GUIDE.md`](./TESTING-GUIDE.md) — Comprehensive testing scenarios
- **Architecture**: [`ARCHITECTURE-COMPARISON.md`](./ARCHITECTURE-COMPARISON.md) — Deep dive vs Node.js
- **Implementation**: [`IMPLEMENTATION-PLAN.md`](./IMPLEMENTATION-PLAN.md) — Full roadmap
- **API Reference**: [`README.md`](./README.md) — API usage examples

---

## Troubleshooting

### Error: "ModuleNotFoundError: No module named 'redis'"

**Solution**: Install with Redis extra
```bash
pip install -e ".[redis]"
```

### Error: "Connection refused" (Redis)

**Solution**: Start Docker Compose
```bash
docker compose up -d redis
docker exec moleculerpy-channels-redis redis-cli ping  # Should return PONG
```

### Error: "Connection refused" (NATS)

**Solution**: Start NATS with JetStream enabled
```bash
docker compose up -d nats
docker exec moleculerpy-channels-nats nc -zv localhost 4222  # Should succeed
```

### Tests hanging

**Solution**: Increase timeouts or check service health
```bash
# Check Docker logs
docker compose logs redis
docker compose logs nats

# Restart services
docker compose restart
```

---

**Done!** You're ready to use MoleculerPy Channels. 🚀

For production deployment, see [`README.md#production-deployment`](./README.md#production-deployment).
