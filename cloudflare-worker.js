// SK하이닉스 대시보드용 전용 CORS 프록시 (Cloudflare Worker, 무료)
// 네이버 금융 / Yahoo Finance 만 중계하도록 제한하여 오·남용 방지.
// 사용법: https://<워커주소>.workers.dev/?url=<대상 URL(인코딩)>

export default {
  async fetch(request) {
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Access-Control-Allow-Headers": "*",
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });

    const url = new URL(request.url);
    const target = url.searchParams.get("url");
    if (!target) return new Response("missing ?url=", { status: 400, headers: cors });

    let host;
    try { host = new URL(target).hostname; } catch (e) {
      return new Response("bad url", { status: 400, headers: cors });
    }
    const allowed = [
      "polling.finance.naver.com",
      "query1.finance.yahoo.com",
      "query2.finance.yahoo.com",
    ];
    if (!allowed.includes(host)) return new Response("host not allowed", { status: 403, headers: cors });

    let resp;
    try {
      resp = await fetch(target, {
        headers: { "User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/" },
      });
    } catch (e) {
      return new Response("upstream error", { status: 502, headers: cors });
    }
    const body = await resp.text();
    return new Response(body, {
      status: resp.status,
      headers: {
        ...cors,
        "Content-Type": resp.headers.get("content-type") || "application/json",
        "Cache-Control": "no-store",
      },
    });
  },
};
