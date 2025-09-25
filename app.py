from flask import Flask, request, jsonify, render_template
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
import threading
from datetime import datetime
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///urine_analyzer.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inisialisasi database
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Model Data untuk SQLite
class UrineTest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date_time = db.Column(db.String(20), nullable=False)
    sample_no = db.Column(db.String(20), nullable=False)
    patient_id = db.Column(db.String(50), nullable=True)
    
    # Hasil tes sebagai JSON string (bisa juga di-normalize)
    results = db.Column(db.Text, nullable=False)  # JSON string
    abnormal_flags = db.Column(db.Text, nullable=False)  # JSON string
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        """Convert database record to dictionary format"""
        return {
            'id': str(self.id),
            'date_time': self.date_time,
            'sample_no': self.sample_no,
            'patient_id': self.patient_id,
            'results': eval(self.results) if isinstance(self.results, str) else self.results,
            'abnormal_flags': eval(self.abnormal_flags) if isinstance(self.abnormal_flags, str) else self.abnormal_flags,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

# Thread-safe data storage (sekarang hanya untuk cache, database sebagai primary)
data_cache = {}
next_id = 1
data_lock = threading.Lock()

# Buat tabel database
with app.app_context():
    db.create_all()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/urine-data', methods=['POST'])
def receive_data():
    global next_id
    try:
        data = request.json
        
        # Validasi struktur dasar
        required_fields = ['results', 'abnormal_flags']
        if not all(field in data for field in required_fields):
            return jsonify({
                'status': 'error',
                'message': 'Data tidak valid: hasil atau flag abnormal tidak ada'
            }), 400
            
        # Validasi format tanggal
        if 'date_time' not in data:
            data['date_time'] = datetime.now().strftime("%Y-%m-%d %H:%M")
        else:
            try:
                datetime.strptime(data['date_time'], "%Y-%m-%d %H:%M")
            except ValueError:
                return jsonify({
                    'status': 'error',
                    'message': 'Format tanggal tidak valid. Gunakan YYYY-MM-DD HH:MM'
                }), 400
        
        with data_lock:
            # Simpan ke database SQLite
            new_test = UrineTest(
                date_time=data['date_time'],
                sample_no=data.get('sample_no', "N/A"),
                patient_id=data.get('patient_id', ""),
                results=str(data['results']),  # Convert dict to string
                abnormal_flags=str(data['abnormal_flags'])  # Convert dict to string
            )
            
            db.session.add(new_test)
            db.session.commit()
            
            # Dapatkan ID yang baru saja di-generate
            data_id = str(new_test.id)
            data['id'] = data_id
            
            # Update cache untuk real-time performance
            data_cache[data_id] = data
            
            # Kirim update real-time
            socketio.emit('new_data', {
                'id': data_id,
                'data': data,
                'type': 'new'
            })
            
        return jsonify({
            'status': 'success',
            'id': data_id,
            'timestamp': data['date_time']
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'status': 'error',
            'message': f'Kesalahan server: {str(e)}'
        }), 500

@app.route('/urine-data/<data_id>', methods=['GET'])
def get_single_data(data_id):
    try:
        # Coba ambil dari cache dulu
        with data_lock:
            if data_id in data_cache:
                return jsonify({'status': 'success', 'data': data_cache[data_id]}), 200
        
        # Jika tidak ada di cache, ambil dari database
        test_record = UrineTest.query.get(int(data_id))
        if test_record:
            return jsonify({'status': 'success', 'data': test_record.to_dict()}), 200
        
        return jsonify({'status': 'error', 'message': 'Data tidak ditemukan'}), 404
    except ValueError:
        return jsonify({'status': 'error', 'message': 'ID tidak valid'}), 400

@app.route('/api/all-data', methods=['GET'])
def get_all_data():
    try:
        # Ambil semua data dari database
        all_tests = UrineTest.query.order_by(UrineTest.created_at.desc()).all()
        
        # Convert ke format yang diharapkan frontend
        data_store = {}
        for test in all_tests:
            data_dict = test.to_dict()
            data_store[data_dict['id']] = data_dict
        
        return jsonify({
            'status': 'success',
            'count': len(data_store),
            'data': data_store
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Kesalahan database: {str(e)}'
        }), 500

@app.route('/api/manual-input', methods=['POST'])
def manual_input():
    """Endpoint untuk testing tanpa alat fisik"""
    try:
        # Generate sample data
        sample_data = {
            "date_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "sample_no": f"TEST-{datetime.now().strftime('%H%M%S')}",
            "patient_id": "SAMPLE-DATA",
            "results": {
                "ubg": "Normal 3.4umol/L",
                "bil": "Neg",
                "ket": "Neg",
                "bld": "1+ Ca25 Ery/uL",
                "pro": "Trace",
                "nit": "Pos",
                "leu": "Neg",
                "glu": "Neg",
                "sg": ">=1.030",
                "ph": "5.5"
            },
            "abnormal_flags": {
                "bld": True,
                "pro": True,
                "nit": True,
                "leu": False,
                "glu": False
            }
        }

        with data_lock:
            # Simpan ke database
            new_test = UrineTest(
                date_time=sample_data['date_time'],
                sample_no=sample_data['sample_no'],
                patient_id=sample_data['patient_id'],
                results=str(sample_data['results']),
                abnormal_flags=str(sample_data['abnormal_flags'])
            )
            
            db.session.add(new_test)
            db.session.commit()
            
            data_id = str(new_test.id)
            sample_data['id'] = data_id
            
            # Update cache
            data_cache[data_id] = sample_data

            socketio.emit('new_data', {
                'id': data_id,
                'data': sample_data,
                'type': 'new'
            })

        return jsonify({
            'status': 'success',
            'id': data_id,
            'timestamp': sample_data['date_time']
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({
            'status': 'error',
            'message': f'Kesalahan server: {str(e)}'
        }), 500

# Endpoint tambahan untuk manajemen data
@app.route('/api/clear-data', methods=['DELETE'])
def clear_all_data():
    """Hapus semua data (hati-hati dengan endpoint ini!)"""
    try:
        # Hapus semua record dari database
        db.session.query(UrineTest).delete()
        db.session.commit()
        
        # Clear cache
        with data_lock:
            data_cache.clear()
        
        return jsonify({'status': 'success', 'message': 'Semua data telah dihapus'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': f'Gagal menghapus data: {str(e)}'}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Endpoint untuk memeriksa status database"""
    try:
        # Test koneksi database
        count = UrineTest.query.count()
        return jsonify({
            'status': 'success',
            'database': 'connected',
            'total_records': count
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'error',
            'database': 'disconnected',
            'message': str(e)
        }), 500

if __name__ == '__main__':
    socketio.run(
        app,
        host='0.0.0.0',
        port=5000,
        debug=True,
        allow_unsafe_werkzeug=True
    )