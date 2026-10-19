# Dependencies are installed in a build stage so the image that ships carries
# neither pip nor setuptools. On a pure-Python Alpine image those two are the
# only things the scanners find, and the exporter needs neither at runtime.
FROM python:3.13-alpine@sha256:1a63a53928ce53d2b0baf08092a703f4840ac5dfbd61fd48802dbf48e08c801e AS build

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.13-alpine@sha256:1a63a53928ce53d2b0baf08092a703f4840ac5dfbd61fd48802dbf48e08c801e

# The base image is pinned by digest, so Alpine fixes published since then are
# applied here; otherwise the scan gate fails on CVEs that already have fixes.
RUN apk upgrade --no-cache \
 && adduser -D -H -u 10001 exporter \
 && rm -rf /usr/local/lib/python3.13/site-packages/pip \
           /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.13/site-packages/setuptools \
           /usr/local/lib/python3.13/site-packages/setuptools-*.dist-info \
           /usr/local/lib/python3.13/site-packages/pkg_resources \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13

COPY --from=build /install /usr/local
WORKDIR /app
COPY exporter.py .

USER exporter
EXPOSE 9835
CMD ["python", "-u", "exporter.py"]
