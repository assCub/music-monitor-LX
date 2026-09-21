FROM golang:1.25-bookworm AS build
WORKDIR /src
COPY go.mod ./
COPY main.go ./
RUN go mod tidy && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /platform-auth ./main.go

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /platform-auth /platform-auth
EXPOSE 8091
ENTRYPOINT ["/platform-auth"]
