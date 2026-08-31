# backend/app/db/redis.py
import redis.asyncio as aioredis
from app.core.config import settings

redis_pool = aioredis.ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)

def get_redis():
    return aioredis.Redis(connection_pool=redis_pool)