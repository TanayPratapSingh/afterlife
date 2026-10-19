# The OpenAlex fetcher as an ECS Fargate task.
#
# Why a container and not Lambda: the keyless fetch of every retracted work
# took 709 metered calls and 9 minutes 42 seconds on a laptop, against Lambda's
# hard 15 minute ceiling. Fargate has no ceiling. See aws/README.md.
#
# Build from the repository root (the context must contain afterlife/):
#
#   docker build --platform linux/arm64 -f aws/container/Dockerfile -t afterlife-openalex .
#
# BuildKit reads aws/container/Dockerfile.dockerignore next to this file, which
# keeps data/, .venv/ and afterlife/spark out of the build context.
#
# For production, pin the base image by digest as well as tag
# (python:3.12-slim@sha256:...), resolved with `docker buildx imagetools
# inspect python:3.12-slim`. It is a tag here because no digest was checked.

FROM python:3.12-slim

# boto3 is the only third party package: S3Store uses it for the landing zone.
# Pinned to the version the moto test suite ran against.
ARG BOTO3_VERSION=1.43.114

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AFTERLIFE_RUNS=/tmp/runs

RUN pip install "boto3==${BOTO3_VERSION}" \
    && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin afterlife

WORKDIR /app
COPY --chown=root:root afterlife/ /app/afterlife/

# The root filesystem is mounted read only in the task definition; run
# reports go to /tmp, which the task mounts as a writable volume.
USER 10001

# A batch task: its health is its exit code, which the state machine and an
# EventBridge rule on ECS task state both watch. There is no process to probe.
HEALTHCHECK NONE

ENTRYPOINT ["python", "-m", "afterlife.cli", "fetch", "openalex"]
CMD ["--budget", "0.09"]
