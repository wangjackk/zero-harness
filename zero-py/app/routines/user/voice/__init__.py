"""voice ---- 语音总线接入包 (sherpa-onnx services zmqproxy listener).

listener 只做订阅消费 (pubsub 'voice' + 日志), 不转发 agent ---- 后续接通.
"""
from .listener import VoiceListener, VoiceListenInput

__all__ = ['VoiceListener', 'VoiceListenInput']
