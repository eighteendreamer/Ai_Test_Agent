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

# Reuse TRANSFER's publish-before-ACK and ownership contract for entries that
# cannot be decoded into a business envelope. Read the original fields inside
# Redis so neither malformed JSON nor delivery metadata needs reinterpretation.
QUARANTINE = _OWNER_CHECK + """
local entries = redis.call('XRANGE', KEYS[1], ARGV[3], ARGV[3])
local fields = {}
if #entries > 0 then fields = entries[1][2] end
local body = cjson.encode({
    source_message_id = ARGV[3], source_group = ARGV[1],
    source_fields = fields, source_entry_missing = #entries == 0,
    dead_letter_reason = ARGV[4]
})
local successor = redis.call('XADD', KEYS[2], '*',
    'payload', body, 'failure_type', ARGV[4],
    'source_message_id', ARGV[3], 'source_group', ARGV[1])
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

# No upstream counterpart (checked: Harness queue/TRANSFER, Redis XTRIM,
# XINFO GROUPS and XPENDING docs): preserve every group's unread/pending
# boundary on Redis >= 6.2, without requiring Redis 8.2's ACKED option.
TRIM_ACKNOWLEDGED = """
local function decimal_less(a, b)
    if #a ~= #b then return #a < #b end
    return a < b
end
local function id_less(a, b)
    local am, as = string.match(a, '^(%d+)%-(%d+)$')
    local bm, bs = string.match(b, '^(%d+)%-(%d+)$')
    if am ~= bm then return decimal_less(am, bm) end
    return decimal_less(as, bs)
end
if redis.call('EXISTS', KEYS[1]) == 0 then return {0, '', 'no_stream'} end
local groups = redis.call('XINFO', 'GROUPS', KEYS[1])
if #groups == 0 then return {0, '', 'no_groups'} end
if #groups > 128 then return {0, '', 'too_many_groups'} end
local clock = redis.call('TIME')
local cutoff = (tonumber(clock[1]) - tonumber(ARGV[1])) * 1000
if cutoff <= 0 then return {0, '', 'retention_window'} end
local threshold = string.format('%.0f', cutoff) .. '-0'
for _, fields in ipairs(groups) do
    local name, cursor
    for i = 1, #fields, 2 do
        if fields[i] == 'name' then name = fields[i + 1] end
        if fields[i] == 'last-delivered-id' then cursor = fields[i + 1] end
    end
    if not name or not cursor then return {0, '', 'unknown_group_boundary'} end
    if id_less(cursor, threshold) then threshold = cursor end
    local pending = redis.call('XPENDING', KEYS[1], name, '-', '+', 1)
    if #pending > 0 and id_less(pending[1][1], threshold) then
        threshold = pending[1][1]
    end
end
-- Redis <= 8.0 MINID does not check pending references. All checks above and
-- trimming must remain in this same atomic script. Keep the boundary itself.
-- Exact trim has no LIMIT option: bound deletion by selecting at most N+1 IDs.
if threshold == '0-0' then return {0, threshold, 'unread_group'} end
local batch = tonumber(ARGV[2])
local entries = redis.call('XRANGE', KEYS[1], '-', '(' .. threshold, 'COUNT', batch + 1)
if #entries > batch then threshold = entries[batch + 1][1] end
local removed = redis.call('XTRIM', KEYS[1], 'MINID', '=', threshold)
return {removed, threshold, ''}
"""
