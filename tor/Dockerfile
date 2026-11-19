FROM alpine:3.21@sha256:bc21487101ae47172509130ee1092cc273a831968d64b617776d1f69e81e42ab

RUN apk add --no-cache tor \
    && mkdir -p /var/lib/tor/hidden_service \
    && chown -R tor:tor /var/lib/tor

COPY torrc /etc/tor/torrc

USER tor
CMD ["tor", "-f", "/etc/tor/torrc"]
