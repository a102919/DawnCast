#!/bin/sh
# Noop placeholder — kept ONLY so Zeabur frozen inline dockerfile (in
# deploy/zeabur-template.yaml) can COPY this path during build.
#
# 真正的 startup 邏輯（migration runner + uvicorn launch）已搬到
# app/main.py lifespan event + Dockerfile CMD 直跑 uvicorn。
# 此 script 不會被執行（CMD 已經是 uvicorn，不是 entrypoint）。

exit 0