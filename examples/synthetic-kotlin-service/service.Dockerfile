FROM scratch AS seed
FROM registry.example.invalid:5000/mercurio/kotlin-service:1.0@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef AS runtime
FROM runtime
