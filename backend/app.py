from flask import Flask, request, jsonify
from flask_cors import CORS
from db import get_db_connection
import pandas as pd
import pdfplumber
import re

app = Flask(__name__)
CORS(app)

@app.route('/')
def home():
    return "Backend Running Successfully"


# -----------------------------
# 🔹 Upload File (PDF / Excel)
# -----------------------------
# -----------------------------
# 🔹 Upload File (PDF / Excel)
# -----------------------------
@app.route("/upload-file", methods=["POST"])
def upload_file():
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]
        filename = file.filename.lower()

        conn = get_db_connection()
        cursor = conn.cursor()

        # 🔹 New Test ID
        cursor.execute("SELECT MAX(test_id) FROM questions")
        last = cursor.fetchone()[0]
        new_test_id = 1 if last is None else last + 1

        inserted = 0
        global_pass_mark = 40

        # ============================================
        # ✅ EXCEL (BEST METHOD)
        # ============================================
        if filename.endswith(".xlsx"):

            df = pd.read_excel(file)

            required_cols = ["reg_no", "question", "test_case"]
            if not all(col in df.columns for col in required_cols):
                return jsonify({
                    "error": "Excel must contain columns: reg_no, question, test_case"
                }), 400

            if "pass_mark" in df.columns:
                val = df["pass_mark"].dropna()
                if not val.empty:
                    global_pass_mark = int(val.iloc[0])

            for _, row in df.iterrows():
                if pd.isna(row["reg_no"]) or pd.isna(row["question"]):
                    continue

                cursor.execute("""
                    INSERT INTO questions (reg_no, question, expected_output, pass_mark, status, test_id)
                    VALUES (%s, %s, %s, %s, 'pending', %s)
                """, (
                    str(row["reg_no"]).strip().upper(),
                    str(row["question"]).strip(),
                    str(row["test_case"]).strip(),
                    global_pass_mark,
                    new_test_id
                ))

                inserted += 1

        # ============================================
        # ✅ PDF (STRICT FORMAT ONLY)
        # ============================================
        elif filename.endswith(".pdf"):

            with pdfplumber.open(file) as pdf:

                full_text = ""

                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        full_text += text + "\n"

                if not full_text.strip():
                    return jsonify({
                        "error": "PDF is empty or unreadable"
                    }), 400

                lines = full_text.split("\n")

                for line in lines:
                    line = line.strip()

                    if not line:
                        continue

                    print("LINE:", line)  # 🔍 DEBUG

                    # 🔹 Extract pass mark
                    if "pass" in line.lower():
                        nums = re.findall(r'\d+', line)
                        if nums:
                            global_pass_mark = int(nums[0])
                        continue

                    # 🔹 REQUIRED FORMAT:
                    # 22BCE001: Question | Answer
                    if ":" in line and "|" in line:
                        try:
                            reg_no, rest = line.split(":", 1)
                            question, test_case = rest.split("|", 1)

                            if not question.strip() or not test_case.strip():
                                continue

                            cursor.execute("""
                                INSERT INTO questions (reg_no, question, expected_output, pass_mark, status, test_id)
                                VALUES (%s, %s, %s, %s, 'pending', %s)
                            """, (
                                reg_no.strip().upper(),
                                question.strip(),
                                test_case.strip(),
                                global_pass_mark,
                                new_test_id
                            ))

                            inserted += 1

                        except Exception as e:
                            print("Skipping line:", line, "Error:", e)

                if inserted == 0:
                    return jsonify({
                        "error": "Invalid PDF format. Use: REG_NO: Question | Answer"
                    }), 400

        else:
            return jsonify({
                "error": "Only .xlsx or .pdf files are supported"
            }), 400

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "message": f"{inserted} questions uploaded successfully",
            "test_id": new_test_id,
            "pass_mark": global_pass_mark
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
# -----------------------------
# 🔹 Submit
# -----------------------------
@app.route('/submit', methods=['POST'])
def submit():
    try:
        data = request.json

        reg_no = data.get("reg_no")
        question_id = data.get("question_id")

        conn = get_db_connection()
        cursor = conn.cursor()

        # 🔴 Prevent duplicate submission
        cursor.execute("SELECT * FROM submissions WHERE reg_no=%s AND question_id=%s", (reg_no, question_id))
        if cursor.fetchone():
            return jsonify({"error": "Already submitted"}), 400

        # 🔴 Get question details
        cursor.execute("SELECT expected_output, pass_mark, test_id FROM questions WHERE id=%s", (question_id,))
        q = cursor.fetchone()

        if not q:
            return jsonify({"error": "Invalid question"}), 400

        expected_output, pass_mark, test_id = q

        # -----------------------------
        # 🔹 TEXT EVALUATION
        # -----------------------------
        def evaluate(text):
            if not text:
                return 0, "Missing"
            elif len(text.strip()) < 20:
                return 5, "Too short"
            else:
                return 10, "Good"

        aim_marks, aim_fb = evaluate(data.get("aim"))
        algo_marks, algo_fb = evaluate(data.get("algorithm"))
        prog_marks, prog_fb = evaluate(data.get("program"))

        # -----------------------------
        # 🔹 TEST CASE BASED OUTPUT CHECK
        # -----------------------------
        student_output = data.get("output", "").lower().strip()
        test_case = expected_output.lower().strip()

        def check_test_case(student, test_case):

            # ✅ Case 1: Exact match
            if student == test_case:
                return 10, "Test case satisfied (exact match)"

            # ✅ Case 2: Contains keywords
            test_words = test_case.split()
            student_words = student.split()

            match_count = sum(1 for word in test_words if word in student_words)

            if len(test_words) > 0:
                ratio = match_count / len(test_words)

                if ratio >= 0.8:
                    return 9, "Test case almost satisfied"
                elif ratio >= 0.5:
                    return 7, "Test case partially satisfied"

            # ✅ Case 3: Numeric validation (important for programs)
            import re
            student_nums = re.findall(r'\d+', student)
            test_nums = re.findall(r'\d+', test_case)

            if test_nums and student_nums:
                common = set(student_nums) & set(test_nums)

                if len(common) == len(test_nums):
                    return 8, "All required numbers present"
                elif len(common) > 0:
                    return 6, "Some values match test case"

            # ❌ Fail
            return 2, "Test case not satisfied"

        output_marks, output_fb = check_test_case(student_output, test_case)

        # -----------------------------
        # 🔹 TOTAL
        # -----------------------------
        total = aim_marks + algo_marks + prog_marks + output_marks
        status = "pass" if total >= pass_mark else "fail"

        # -----------------------------
        # 🔹 CHEATING DETECTION
        # -----------------------------
        paste_count = data.get("paste_count", 0)
        time_taken = data.get("time_taken", 0)

        cheating_flag = "yes" if paste_count > 3 or time_taken < 30 else "no"

        # -----------------------------
        # 🔹 FINAL FEEDBACK
        # -----------------------------
        feedback = f"""
AIM: {aim_fb}
ALGORITHM: {algo_fb}
PROGRAM: {prog_fb}
TEST CASE: {output_fb}
"""

        # -----------------------------
        # 🔹 SAVE TO DB
        # -----------------------------
        cursor.execute("""
            INSERT INTO submissions
            (reg_no, question_id, aim, algorithm, program, output,
             total_marks, feedback, status, test_id, paste_count, time_taken, cheating_flag)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            reg_no.upper(),
            question_id,
            data.get("aim"),
            data.get("algorithm"),
            data.get("program"),
            student_output,
            total,
            feedback,
            status,
            test_id,
            paste_count,
            time_taken,
            cheating_flag
        ))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "total_marks": total,
            "status": status,
            "feedback": feedback,
            "cheating_flag": cheating_flag
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -----------------------------
# 🔹 Get Question
# -----------------------------
@app.route('/get-question/<reg_no>', methods=['GET'])
def get_question(reg_no):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT MAX(test_id) AS test_id FROM questions")
    current_test = cursor.fetchone()["test_id"]

    cursor.execute("""
        SELECT * FROM questions
        WHERE reg_no=%s AND test_id=%s
        ORDER BY id DESC LIMIT 1
    """, (reg_no, current_test))

    data = cursor.fetchone()

    cursor.close()
    conn.close()

    return jsonify(data if data else {})


# -----------------------------
# 🔹 Results
# -----------------------------
@app.route('/results', methods=['GET'])
def results():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # 🔹 Get latest test
        cursor.execute("SELECT MAX(test_id) AS test_id FROM submissions")
        row = cursor.fetchone()
        current_test = row["test_id"]

        if current_test is None:
            return jsonify({
                "current": [],
                "previous": []
            })

        # ✅ Current test results
        cursor.execute("""
            SELECT * FROM submissions 
            WHERE test_id = %s
            ORDER BY id DESC
        """, (current_test,))
        current = cursor.fetchall()

        # ✅ Previous test results
        cursor.execute("""
            SELECT * FROM submissions 
            WHERE test_id < %s
            ORDER BY id DESC
        """, (current_test,))
        previous = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            "current": current,
            "previous": previous
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
# -----------------------------
# 🔹 Test Stats (FIXED)
# -----------------------------
@app.route('/test-stats', methods=['GET'])
def test_stats():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 🔥 Get current test
        cursor.execute("SELECT MAX(test_id) FROM questions")
        current_test = cursor.fetchone()[0]

        if current_test is None:
            return jsonify({
                "total": 0,
                "attended": 0,
                "remaining": 0
            })

        # 🔥 Total students assigned
        cursor.execute("""
            SELECT COUNT(DISTINCT reg_no)
            FROM questions
            WHERE test_id=%s
        """, (current_test,))
        total = cursor.fetchone()[0] or 0

        # 🔥 Students attended
        cursor.execute("""
            SELECT COUNT(DISTINCT reg_no)
            FROM submissions
            WHERE test_id=%s
        """, (current_test,))
        attended = cursor.fetchone()[0] or 0

        remaining = total - attended

        cursor.close()
        conn.close()

        return jsonify({
            "total": total,
            "attended": attended,
            "remaining": remaining
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
# -----------------------------
# 🔹 Update Teacher Feedback
# -----------------------------
@app.route('/update-feedback', methods=['POST'])
def update_feedback():
    try:
        data = request.json

        submission_id = data.get("id")
        feedback = data.get("teacher_feedback")

        if not submission_id:
            return jsonify({"error": "Missing submission ID"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE submissions
            SET teacher_feedback=%s
            WHERE id=%s
        """, (feedback, submission_id))

        conn.commit()

        cursor.close()
        conn.close()

        return jsonify({"message": "Feedback updated successfully"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
@app.route('/analytics', methods=['GET'])
def get_analytics():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT MAX(test_id) AS test_id FROM submissions")
        current_test = cursor.fetchone()["test_id"]

        if current_test is None:
            return jsonify({
                "total": 0,
                "passed": 0,
                "failed": 0,
                "average": 0
            })

        cursor.execute("""
            SELECT COUNT(*) AS total 
            FROM submissions 
            WHERE test_id = %s
        """, (current_test,))
        total = cursor.fetchone()["total"]

        cursor.execute("""
            SELECT COUNT(*) AS pass_count 
            FROM submissions 
            WHERE status='pass' AND test_id=%s
        """, (current_test,))
        passed = cursor.fetchone()["pass_count"]

        cursor.execute("""
            SELECT COUNT(*) AS fail_count 
            FROM submissions 
            WHERE status='fail' AND test_id=%s
        """, (current_test,))
        failed = cursor.fetchone()["fail_count"]

        cursor.execute("""
            SELECT AVG(total_marks) AS avg_marks 
            FROM submissions 
            WHERE test_id=%s
        """, (current_test,))
        avg = cursor.fetchone()["avg_marks"]

        cursor.close()
        conn.close()

        return jsonify({
            "total": total,
            "passed": passed,
            "failed": failed,
            "average": round(avg, 2) if avg else 0
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/previous-results', methods=['GET'])
def previous_results():
    try:
        reg_no = request.args.get("reg_no")

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        if reg_no:
            cursor.execute("""
                SELECT * FROM submissions 
                WHERE reg_no = %s 
                ORDER BY id DESC
            """, (reg_no,))
        else:
            cursor.execute("""
                SELECT * FROM submissions 
                ORDER BY id DESC
            """)

        data = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify(data)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
# -----------------------------
# 🔹 Run
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True)