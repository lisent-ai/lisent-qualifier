"""
EventPort adapters.

Skor update + session event yayını:
    - RedisPubSubAdapter — Redis PubSub (score:{session_id} channel) + ZSET score history
    - Phase 6+: SSEBroadcastAdapter (live score board), WebhookFanoutAdapter (tenant outbound event'leri)
"""

from app.adapters.event.redis_pubsub import RedisPubSubAdapter

__all__ = ["RedisPubSubAdapter"]
