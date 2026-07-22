#!/bin/sh
# Noop placeholder — kept ONLY so Zeabur frozen inline dockerfile (in
# deploy/zeabur-template.yaml) can COPY this path during build.
#
# 真正的 startup 邏輯（migration runner + worker poll）已搬到
# engine/worker.py main() + Dockerfile CMD 直跑 python -m engine.worker。
# 此 script 不會被執行（CMD 已經是 python -m engine.worker，不是 entrypoint）。

exit 0