import sys, json
data = sys.stdin.read()
lines = data.split("\n")
lines = [l for l in lines if l.strip()]
joined = "\n".join(lines)
result = {"joined": joined, "count": len(lines)}
# Output as JSON
sys.stdout.write(json.dumps(result))