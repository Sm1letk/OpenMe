import os, re, json
from flask import Flask, request, Response, stream_with_context
from openai import OpenAI
from dotenv import load_dotenv
from stream import filter_think_stream
import persona

load_dotenv()

MODEL = "MiniMax-M2.7"
ai = OpenAI(
    api_key=os.environ["MINIMAX_API_KEY"],
    base_url="https://api.minimaxi.com/v1",
)

app = Flask(__name__)
sessions: dict[str, list] = {}
MAX_SESSIONS = 500


SYSTEM = persona.build_system("public")


@app.route("/api/chat", methods=["POST"])
def chat():
    data    = request.get_json(force=True)
    text    = (data.get("message") or "").strip()
    session = data.get("session_id", "default")

    if not text:
        return {"error": "empty message"}, 400

    hist = sessions.setdefault(session, [])
    if len(sessions) > MAX_SESSIONS:
        oldest = next(iter(sessions))
        del sessions[oldest]
    hist.append({"role": "user", "content": text})
    if len(hist) > 20:
        hist[:] = hist[-20:]

    def generate():
        full_text = []
        try:
            stream = ai.chat.completions.create(
                model=MODEL,
                max_tokens=512,
                stream=True,
                messages=[{"role": "system", "content": SYSTEM}] + hist,
            )
            for text_chunk in filter_think_stream(stream):
                full_text.append(text_chunk)
                yield f"data: {json.dumps({'text': text_chunk})}\n\n"

            # 保存完整回复到历史，支持正常多轮对话
            hist.append({"role": "assistant", "content": "".join(full_text)})
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.route("/api/chat", methods=["OPTIONS"])
def chat_options():
    return "", 204, {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
