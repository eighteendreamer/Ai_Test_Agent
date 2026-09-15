"""Redis Lua for lease ownership, fencing and hierarchical quota accounting."""

CLAIM = """
local clock = redis.call('TIME')
local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
local expired = redis.call('ZRANGEBYSCORE', KEYS[5], '-inf', tostring(now), 'LIMIT', 0, 100)
for _, member in ipairs(expired) do
  redis.call('ZREM', KEYS[5], member)
  if redis.call('EXISTS', member) == 0 then
    local raw = redis.call('HGET', KEYS[6], member)
    if raw then
      local fields = cjson.decode(raw)
      if fields['quota'] then fields = fields['quota'] end
      for _, field in ipairs(fields) do
        local current = tonumber(redis.call('HINCRBY', KEYS[3], field, -1))
        if current <= 0 then redis.call('HDEL', KEYS[3], field) end
      end
      redis.call('HDEL', KEYS[6], member)
    end
  end
end
if redis.call('EXISTS', KEYS[1]) == 1 then return {0, 'resource_busy'} end
local quota_count = #ARGV - 3
for i = 1, quota_count do
  local limit = tonumber(redis.call('HGET', KEYS[4], ARGV[3 + i]) or '0')
  local used = tonumber(redis.call('HGET', KEYS[3], ARGV[3 + i]) or '0')
  if limit > 0 and used >= limit then return {0, ARGV[3 + i]} end
end
local token = redis.call('INCR', KEYS[2])
redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[3])
for i = 1, quota_count do redis.call('HINCRBY', KEYS[3], ARGV[3 + i], 1) end
redis.call('ZADD', KEYS[5], tostring(now + tonumber(ARGV[3])), KEYS[1])
local fields = {}
for i = 1, quota_count do fields[i] = ARGV[3 + i] end
redis.call('HSET', KEYS[6], KEYS[1], cjson.encode({quota = fields}))
return {token, 'ok'}
"""

REAP = """
local clock = redis.call('TIME')
local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', tostring(now), 'LIMIT', 0, ARGV[1])
local result = {}
for _, member in ipairs(expired) do
  redis.call('ZREM', KEYS[1], member)
  if redis.call('EXISTS', member) == 0 then
    local raw = redis.call('HGET', KEYS[3], member)
    if raw then
      local fields = cjson.decode(raw)
      if fields['quota'] then fields = fields['quota'] end
      for _, field in ipairs(fields) do
        local current = tonumber(redis.call('HINCRBY', KEYS[2], field, -1))
        if current <= 0 then redis.call('HDEL', KEYS[2], field) end
      end
      redis.call('HDEL', KEYS[3], member)
    end
    table.insert(result, member)
  end
end
return result
"""

RENEW = """
local raw = redis.call('GET', KEYS[1])
if raw then
  local payload = cjson.decode(raw)
  if payload['lease_token'] == ARGV[1] then
    local result = redis.call('PEXPIRE', KEYS[1], ARGV[2])
    local clock = redis.call('TIME')
    redis.call('ZADD', KEYS[2], tostring(clock[1] * 1000 + math.floor(clock[2] / 1000) + tonumber(ARGV[2])), KEYS[1])
    return result
  end
end
return 0
"""

RELEASE = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local payload = cjson.decode(raw)
if payload['lease_token'] ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
redis.call('ZREM', KEYS[3], KEYS[1])
redis.call('HDEL', KEYS[4], KEYS[1])
for i = 1, #ARGV - 1 do
  local current = tonumber(redis.call('HINCRBY', KEYS[2], ARGV[i + 1], -1))
  if current <= 0 then redis.call('HDEL', KEYS[2], ARGV[i + 1]) end
end
return 1
"""

BIND = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local payload = cjson.decode(raw)
if payload['lease_token'] ~= ARGV[1] then return 0 end
payload['external_resource_id'] = ARGV[2]
redis.call('SET', KEYS[1], cjson.encode(payload), 'KEEPTTL')
return 1
"""
