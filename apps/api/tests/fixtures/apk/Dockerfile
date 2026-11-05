# Sadece test fixture'larını üretmek için (bkz. build_fixtures.py). Üretimde kullanılmaz.
FROM eclipse-temurin:21-jdk
ARG APKTOOL_VERSION=3.0.3
RUN apt-get update && apt-get install -y --no-install-recommends python3 curl ca-certificates \
    && curl -fsSL -o /opt/apktool.jar \
       "https://github.com/iBotPeaches/Apktool/releases/download/v${APKTOOL_VERSION}/apktool_${APKTOOL_VERSION}.jar" \
    && apt-get purge -y curl && rm -rf /var/lib/apt/lists/*
COPY build_fixtures.py /build_fixtures.py
CMD ["python3", "/build_fixtures.py"]
