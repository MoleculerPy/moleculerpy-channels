# MoleculerPy Channels — Gap Analysis vs moleculer-channels

**Date**: 2026-01-30
**Source Analysis**: moleculer-channels v1.x (Node.js)
**Current Status**: MoleculerPy Channels Phase 3.2 Complete

---

## Executive Summary

**Overall Compatibility**: ~60% (Core messaging ✅, Advanced features ⏸️)

**Critical Gaps (P0)**: 3 features blocking production use
**High Priority (P1)**: 3 features for operational excellence  
**Nice-to-Have (P2)**: 4 features for observability/scalability

---

## 1. Feature Comparison Matrix

| Feature | moleculer-channels | MoleculerPy Channels | Status | Priority |
|---------|-------------------|-------------------|--------|----------|
| **Core Messaging** |
| Publish/Subscribe | ✅ Full | ✅ Full | ✅ 100% | - |
| Serialization | ✅ JSON/MsgPack/Avro | ✅ JSON | ⏸️ 33% | P2 |
| Headers | ✅ Full | ✅ Full | ✅ 100% | - |
| **Reliability** |
| Consumer Groups | ✅ Full (XGROUP) | ❌ **Missing** | ❌ 0% | **P0** |
| ACK/NACK | ✅ Full | ✅ Full | ✅ 100% | - |
| Retry Logic | ✅ XAUTOCLAIM + cursor | ⏸️ Basic retry | ⏸️ 40% | **P0** |
| DLQ | ✅ Full with metadata | ⏸️ Basic DLQ | ⏸️ 60% | **P0** |
| **Advanced Features** |
| Error Metadata Storage | ✅ Redis Hash (TTL 24h) | ❌ **Missing** | ❌ 0% | **P0** |
| Cursor-based XAUTOCLAIM | ✅ Prevents duplicates | ❌ **Missing** | ❌ 0% | **P0** |
| maxInFlight Backpressure | ✅ Full | ⏸️ Partial | ⏸️ 50% | P1 |
| Graceful Shutdown | ✅ Waits for active | ✅ Implemented | ✅ 100% | - |
| Context Propagation | ✅ Full ($requestID, etc) | ⏸️ Basic | ⏸️ 70% | P1 |
| **Observability** |
| Metrics | ✅ 7 metrics | ❌ **Missing** | ❌ 0% | P1 |
| Logging | ✅ Debug/Info | ✅ Full | ✅ 100% | - |
| **Scalability** |
| Multiple Adapters | ✅ 6 adapters | ⏸️ 1 adapter (Redis) | ⏸️ 17% | P2 |
| Redis Cluster | ✅ Full | ❓ Untested | ❓ 0% | P2 |
| Capped Streams (MAXLEN) | ✅ Full | ⏸️ Partial | ⏸️ 80% | P2 |

**Legend:**
- ✅ Full implementation
- ⏸️ Partial implementation  
- ❌ Missing/Not implemented
- ❓ Unknown/Untested

---

## 2. Critical Missing Features (P0) 🚨

### 2.1 Consumer Groups ❌

**What moleculer-channels does:**
```javascript
// Auto-creates consumer group on subscribe
await redis.xgroup('CREATE', streamName, groupName, '$', 'MKSTREAM')

// Reads from group (distributed across consumers)
await redis.xreadgroup(
  'GROUP', groupName, consumerID,
  'COUNT', maxInFlight,
  'BLOCK', timeout,
  'STREAMS', streamName, '>'
)
```

**Impact:**
- ❌ **No horizontal scaling** — All instances consume ALL messages
- ❌ **Duplicate processing** — Same message handled by multiple instances
- ❌ **No load balancing** — Cannot distribute work

**Required Implementation:**
1. XGROUP CREATE on subscribe (with MKSTREAM)
2. XREADGROUP instead of XREAD
3. Consumer ID generation (unique per instance)
4. Group management (cleanup on unsubscribe)

**Complexity**: Medium (~150 LOC)
**Timeline**: 1-2 days

---

### 2.2 Cursor-Based XAUTOCLAIM ❌

**What moleculer-channels does:**
```javascript
// Maintains cursor across iterations
let cursorID = "0-0"

const claimLoop = async () => {
  const [newCursor, messages] = await redis.xautoclaim(
    streamName, groupName, consumerID,
    minIdleTime,  // 3600000ms (1 hour)
    cursorID,     // Previous position
    'COUNT', maxInFlight
  )
  
  cursorID = newCursor  // Update for next iteration
  
  // Process claimed messages
  await Promise.allSettled(messages.map(processMessage))
  
  setTimeout(claimLoop, claimInterval)  // 100ms
}
```

**Why Cursor Matters:**
- ✅ **Prevents re-processing** — Tracks scan position
- ✅ **Efficient scanning** — Doesn't re-check processed messages
- ✅ **Fairness** — Eventually claims all pending messages
- ✅ **Loops automatically** — Returns "0-0" when scan completes

**Impact without cursor:**
- ⚠️ **Repeated work** — Same pending messages checked every iteration
- ⚠️ **Inefficiency** — Wasted Redis calls
- ⚠️ **Potential hot spots** — Early messages claimed repeatedly

**Current MoleculerPy Implementation:**
```python
# Simple XAUTOCLAIM without cursor
messages = await self.redis.xautoclaim(
    name=channel.name,
    groupname=channel.group,
    consumername=self.consumer_id,
    min_idle_time=min_idle_time,
    count=10,
    start_id="0-0"  # ❌ ALWAYS starts from beginning!
)
```

**Required Implementation:**
1. Store cursor per channel: `self._claim_cursors: dict[str, str]`
2. Initialize cursor: `self._claim_cursors[channel.id] = "0-0"`
3. Update cursor after each claim: `self._claim_cursors[channel.id] = new_cursor`
4. Reset to "0-0" on loop completion

**Complexity**: Low (~50 LOC)
**Timeline**: 2-3 hours

---

### 2.3 Error Metadata Storage (Redis Hash) ❌

**What moleculer-channels does:**

**On Handler Failure:**
```javascript
// Store error metadata in Redis Hash
await redis.hset(
  `chan:${channelName}:msg:${messageID}`,
  'x-error-message', error.message,
  'x-error-code', error.code || 500,
  'x-error-type', error.type || 'Error',
  'x-error-name', error.name,
  'x-error-retryable', String(error.retryable ?? true),
  'x-error-stack', Buffer.from(error.stack).toString('base64'),
  'x-error-data', Buffer.from(JSON.stringify(error.data)).toString('base64'),
  'x-error-timestamp', Date.now()
)

// Set TTL
await redis.expire(`chan:${channelName}:msg:${messageID}`, errorInfoTTL)  // 24h
```

**On DLQ Move:**
```javascript
// Retrieve error metadata
const errorInfo = await redis.hgetall(`chan:${channelName}:msg:${messageID}`)

// Add to DLQ message headers
await redis.xadd(
  dlqStreamName, '*',
  'payload', originalPayload,
  'headers', JSON.stringify({
    ...originalHeaders,
    ...errorInfo,  // ← Full error context!
    'x-original-id': messageID,
    'x-original-channel': channelName,
    'x-original-group': groupName
  })
)

// Cleanup
await redis.del(`chan:${channelName}:msg:${messageID}`)
await redis.xack(streamName, groupName, messageID)
```

**Impact:**
- ✅ **Full error context** — Stack trace, error data, timestamp
- ✅ **Debugging enabled** — Can analyze failure reasons
- ✅ **Error recovery** — Error data aids in retry logic
- ✅ **Audit trail** — Timestamp shows when error occurred

**Current MoleculerPy Implementation:**
```python
# Basic DLQ without error metadata
await self.redis.xadd(
    dlq_stream,
    {
        b"payload": payload_bytes,
        b"headers": headers_bytes  # ❌ No error info!
    }
)
```

**Required Implementation:**
1. `async def _store_error_metadata(channel, msg_id, error)` — HSET with error fields
2. `async def _get_error_metadata(channel, msg_id)` — HGETALL error hash
3. `async def _delete_error_metadata(channel, msg_id)` — DEL hash
4. Integrate with `_process_message()` exception handler
5. Integrate with `_move_to_dlq()` to include error headers

**Complexity**: Medium (~100 LOC)
**Timeline**: 4-6 hours

---

## 3. High Priority Features (P1) ⚠️

### 3.1 maxInFlight Backpressure Control

**What moleculer-channels does:**
```javascript
// Before reading/claiming:
const activeCount = this.activeMessages.get(channelID)?.size || 0
const availableCapacity = maxInFlight - activeCount

if (availableCapacity > 0) {
  // Only read what we can handle
  const messages = await redis.xreadgroup(
    ...
    'COUNT', availableCapacity,  // ← Respect capacity!
    ...
  )
}
```

**Impact:**
- ✅ **Prevents overload** — Limits concurrent processing
- ✅ **Memory control** — Bounded in-flight messages
- ✅ **Graceful degradation** — System stable under load

**Current MoleculerPy:**
- ⏸️ Basic tracking exists (`add_channel_active_messages`)
- ❌ Not integrated with XREADGROUP COUNT

**Required**: Integrate active message tracking with read limits

**Complexity**: Low (~30 LOC)
**Timeline**: 2-3 hours

---

### 3.2 Full Context Propagation

**What moleculer-channels does:**
```javascript
// Serialize context to headers
const headers = {
  '$requestID': ctx.requestID,
  '$parentID': ctx.id,
  '$tracing': String(ctx.tracing),
  '$level': String(ctx.level + 1),
  '$caller': ctx.service.name,
  '$parentChannelName': channelName,
  '$meta': Buffer.from(JSON.stringify(ctx.meta)).toString('base64'),
  '$headers': Buffer.from(JSON.stringify(ctx.headers)).toString('base64')
}

// Deserialize and create context on consume
const newContext = new Context({
  requestID: headers['$requestID'],
  parentID: headers['$parentID'],
  level: parseInt(headers['$level']),
  meta: JSON.parse(Buffer.from(headers['$meta'], 'base64')),
  // ... etc
})
```

**Impact:**
- ✅ **Distributed tracing** — Track requests across channels
- ✅ **Metadata flow** — User data propagates automatically
- ✅ **Debugging** — Full request context available

**Current MoleculerPy:**
- ⏸️ Basic header support
- ❌ No automatic context serialization

**Required**: Context <-> Headers serialization utilities

**Complexity**: Medium (~80 LOC)
**Timeline**: 3-4 hours

---

### 3.3 Comprehensive Metrics

**What moleculer-channels does:**
```javascript
// 7 different metrics
metrics.increment('moleculer.channels.messages.sent', { channel })
metrics.increment('moleculer.channels.messages.total', { channel })
metrics.set('moleculer.channels.messages.active', activeCount, { channel })
metrics.observe('moleculer.channels.messages.time', duration, { channel })
metrics.increment('moleculer.channels.messages.errors.total', { channel, errorType })
metrics.increment('moleculer.channels.messages.retries.total', { channel })
metrics.increment('moleculer.channels.messages.deadLettering.total', { channel })
```

**Impact:**
- ✅ **Observability** — Monitor channel health
- ✅ **Alerting** — Detect anomalies
- ✅ **Performance tuning** — Identify bottlenecks

**Current MoleculerPy:**
- ✅ Debug logging exists
- ❌ No structured metrics

**Required**: Metrics abstraction + Prometheus exporter

**Complexity**: Medium (~120 LOC)
**Timeline**: 4-6 hours

---

## 4. Nice-to-Have Features (P2) 💡

### 4.1 Multiple Adapter Support

Allow same broker to use different transports per channel.

**Complexity**: High (~200 LOC)
**Timeline**: 1-2 weeks

---

### 4.2 Redis Cluster Support

Test and document ioredis cluster mode compatibility.

**Complexity**: Low (testing + docs)
**Timeline**: 1 day

---

### 4.3 Multiple Serializers

Add MsgPack, Avro support alongside JSON.

**Complexity**: Low (~80 LOC)
**Timeline**: 2-3 hours

---

### 4.4 Enhanced MAXLEN Support

Already partially implemented, needs testing.

**Complexity**: Low (~20 LOC)
**Timeline**: 1 hour

---

## 5. Implementation Roadmap

### Phase 4.1: Critical Gaps (P0) — 2-3 days

**Priority Order:**
1. ✅ **Consumer Groups** (Day 1-2)
   - XGROUP CREATE/DESTROY
   - XREADGROUP integration
   - Consumer ID generation
   - Tests

2. ✅ **Cursor-based XAUTOCLAIM** (Day 2)
   - Cursor tracking per channel
   - Update after each claim
   - Loop detection (0-0 reset)
   - Tests

3. ✅ **Error Metadata Storage** (Day 2-3)
   - HSET/HGETALL/DEL utilities
   - Error serialization (stack, data, etc)
   - DLQ integration
   - Tests

**Deliverable**: Production-ready Redis adapter with full feature parity

---

### Phase 4.2: High Priority (P1) — 1-2 days

**Priority Order:**
1. ✅ **maxInFlight Backpressure** (4 hours)
2. ✅ **Context Propagation** (4 hours)
3. ✅ **Metrics System** (6 hours)

**Deliverable**: Operational excellence features

---

### Phase 4.3: Nice-to-Have (P2) — Optional

Implement as needed for specific use cases.

---

## 6. Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| **No Consumer Groups** | 🔴 Critical - No horizontal scaling | Implement in Phase 4.1 |
| **No Cursor XAUTOCLAIM** | 🟡 Medium - Inefficient retries | Implement in Phase 4.1 |
| **No Error Metadata** | 🟡 Medium - Hard to debug DLQ | Implement in Phase 4.1 |
| **Limited Metrics** | 🟢 Low - Can use logging | Implement in Phase 4.2 |
| **Single Serializer** | 🟢 Low - JSON is universal | Optional upgrade |

---

## 7. Compatibility Score

### Current (Phase 3.2 Complete):
- **Core Messaging**: 90% ✅
- **Reliability**: 60% ⏸️
- **Advanced Features**: 40% ⏸️
- **Observability**: 30% ⏸️
- **Overall**: **60%**

### After Phase 4.1 (P0 Fixes):
- **Core Messaging**: 95% ✅
- **Reliability**: 95% ✅
- **Advanced Features**: 80% ✅
- **Observability**: 30% ⏸️
- **Overall**: **85%**

### After Phase 4.2 (P1 Fixes):
- **Core Messaging**: 100% ✅
- **Reliability**: 100% ✅
- **Advanced Features**: 90% ✅
- **Observability**: 80% ✅
- **Overall**: **95%**

---

## 8. Conclusion

**Current Status**: MoleculerPy Channels is **60% feature-complete** compared to moleculer-channels.

**Critical Blockers**: 3 P0 features prevent production use:
1. Consumer Groups (horizontal scaling)
2. Cursor-based XAUTOCLAIM (efficient retry)
3. Error Metadata Storage (debugging)

**Recommendation**: Implement Phase 4.1 (P0 fixes) to reach **85% compatibility** and production-readiness.

**Estimated Effort**: 2-3 developer-days for P0, +1-2 days for P1.

---

**Report Generated**: 2026-01-30  
**Analyzed By**: Sub-task Agent (a5aaaf2)  
**Version**: 1.0 (Phase 3.2 Complete)
