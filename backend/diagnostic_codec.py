"""Lossless diagnostic-only storage. Financial/configuration rows stay JSON."""
import base64
import json
import zlib

PREFIX='PTD-ZLIB-JSON-1:'
MAX_DECODED_BYTES=32*1024*1024


def encode_payload(payload):
    raw=json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    data=raw.encode('utf-8')
    if len(data)>MAX_DECODED_BYTES:raise ValueError('Diagnóstico excede o tamanho máximo de armazenamento.')
    if len(data)<2048:return raw
    packed=PREFIX+base64.b64encode(zlib.compress(data,6)).decode('ascii')
    return packed if len(packed)<len(data)*.9 else raw


def decode_payload(raw):
    if raw.startswith(PREFIX):
        packed=base64.b64decode(raw[len(PREFIX):],validate=True)
        decoder=zlib.decompressobj()
        try:data=decoder.decompress(packed,MAX_DECODED_BYTES+1)
        except zlib.error:raise ValueError('Diagnóstico comprimido inválido.') from None
        if len(data)>MAX_DECODED_BYTES or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
            raise ValueError('Diagnóstico comprimido inválido ou maior que o limite.')
        raw=data.decode('utf-8')
    return json.loads(raw)
