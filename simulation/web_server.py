"""Flask web server for the simulator dashboard and code tester."""
from flask import Flask, render_template, request, jsonify
from simulation.scene_manager import DualArmScene

app = Flask(__name__)
scene = DualArmScene()

@app.route("/")
def index():
    return render_template("dashboard.html")

@app.route("/move", methods=["POST"])
def move():
    data = request.json
    arm = data["arm"]
    q = data["q"]   # list of 6 joint angles
    scene.move_to(arm, q)
    return jsonify({"status": "ok"})

@app.route("/run_segments", methods=["POST"])
def run_segments():
    from simulation.code_tester import run_sequence
    segments = request.json["segments"]
    run_sequence(scene, segments)
    return jsonify({"status": "finished"})

if __name__ == "__main__":
    app.run(debug=True, port=5000)