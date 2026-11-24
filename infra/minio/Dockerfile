FROM golang:1.26-alpine AS build
WORKDIR /src
ARG RELEASE=RELEASE.2025-10-15T17-29-55Z
ADD --checksum=sha256:be6d0bd3696c3a13a35f02d3a0280b64319c67918b4501c5c3d87f96d000085c https://codeload.github.com/minio/minio/tar.gz/refs/tags/${RELEASE} /tmp/minio.tar.gz
RUN tar -xzf /tmp/minio.tar.gz --strip-components=1
RUN CGO_ENABLED=0 go build -mod=readonly -tags kqueue -trimpath \
    -ldflags="-s -w -X github.com/minio/minio/cmd.Version=2025-10-15T17:29:55Z -X github.com/minio/minio/cmd.ReleaseTag=${RELEASE}" \
    -o /out/minio .

FROM alpine:3.23
COPY --from=build /out/minio /usr/local/bin/minio
COPY --from=build /src/LICENSE /usr/share/licenses/minio/LICENSE
ENTRYPOINT ["minio"]
