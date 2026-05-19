import os, re, json
from flask import Flask, request, Response, stream_with_context
from openai import OpenAI
from dotenv import load_dotenv
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
        buffer = ""
        full_text = []
        try:
            stream = ai.chat.completions.create(
                model=MODEL,
                max_tokens=512,
                stream=True,
                messages=[{"role": "system", "content": SYSTEM}] + hist,
            )
            in_think = False
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                buffer += delta

                # 过滤 <think>...</think>
                while True:
                    if in_think:
                        end = buffer.find("</think>")
                        if end == -1:
                            buffer = ""
                            break
                        buffer = buffer[end + 8:]
                        in_think = False
                    else:
                        start = buffer.find("<think>")
                        if start == -1:
                            full_text.append(buffer)
                            yield f"data: {json.dumps({'text': buffer})}\n\n"
                            buffer = ""
                            break
                        if start > 0:
                            full_text.append(buffer[:start])
                            yield f"data: {json.dumps({'text': buffer[:start]})}\n\n"
                        buffer = buffer[start + 7:]
                        in_think = True

            if buffer and not in_think:
                full_text.append(buffer)
                yield f"data: {json.dumps({'text': buffer})}\n\n"

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
