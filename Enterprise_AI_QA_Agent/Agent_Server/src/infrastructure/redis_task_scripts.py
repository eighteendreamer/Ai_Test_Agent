"""Ownership-checked delivery operations for the standalone Harness Redis.

Redis XPENDING/XCLAIM/XADD/XACK semantics are exercised by live delivery tests.
Scripts are atomic with respect to other clients, but Redis does not roll back
Lua errors: publish MUST precede ACK so a failed publish retains the original.
"""

_OWNER_CHECK = """
local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[3], ARGV[3], 1)
if #pending == 0 or pending[1][2] ~= ARGV[2] then return false end
"""

TRANSFER = _OWNER_CHECK + """
local clock = redis.call('TIME')
local due = clock[1] * 1000 + math.floor(clock[2] / 1000) + tonumber(ARGV[6])
local successor = redis.call('XADD', KEYS[2], '*',
    'payload', ARGV[4], 'retry_count', ARGV[5],
    'not_before_ms', tostring(due), 'failure_type', ARGV[7])
redis.call('XACK', KEYS[1], ARGV[1], ARGV[3])
return successor
"""

CONTROL = _OWNER_CHECK + """
if ARGV[4] == 'ack' then
    return redis.call('XACK', KEYS[1], ARGV[1], ARGV[3])
end
if ARGV[4] == 'touch' then
    redis.call('XCLAIM', KEYS[1], ARGV[1], ARGV[2], 0, ARGV[3], 'JUSTID')
    return 1
end
local clock = redis.call('TIME')
local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
if now < tonumber(ARGV[5]) then return false end
return 1
"""
