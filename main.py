import os
import uuid
import requests
import m3u8
import ffmpeg
from flask import Flask, render_template, request, send_file, jsonify
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

class M3U8DownloadManager:
   def __init__(self, playlist_url):
       self.playlist_url = playlist_url
       self.output_dir = os.path.join('downloads', str(uuid.uuid4()))
       os.makedirs(self.output_dir, exist_ok=True)
       self.playlist = self._parse_playlist()

   def _parse_playlist(self):
    try:
        response = requests.get(self.playlist_url, timeout=10)
        response.raise_for_status()
        return m3u8.loads(response.text)
    except requests.exceptions.RequestException as e:
        print(f"Playlist parsing error: {e}")
        return None
    except m3u8.M3U8Error as e:
        print(f"M3U8 parsing error: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error: {e}")
        return None

   def get_available_streams(self):
       if not self.playlist or not self.playlist.playlists:
           return []
       return [f"{pl.stream_info.resolution[0]}x{pl.stream_info.resolution[1]}" for pl in self.playlist.playlists]

   def get_available_audio_tracks(self):
       if not self.playlist or not self.playlist.media:
           return []
       return [media.name for media in self.playlist.media if media.type == 'AUDIO']

   def download_stream(self, resolution=None, language=None, max_workers=5):
       available_streams = self.get_available_streams()
       available_audio_tracks = self.get_available_audio_tracks()

       resolution = resolution or (available_streams[0] if available_streams else None)
       language = language or (available_audio_tracks[0] if available_audio_tracks else None)

       if not resolution or not language:
           raise ValueError("No streams or audio tracks available")

       stream_playlist = next((pl for pl in self.playlist.playlists if f"{pl.stream_info.resolution[0]}x{pl.stream_info.resolution[1]}" == resolution), None)
       audio_track = next((media for media in self.playlist.media if media.type == 'AUDIO' and media.name == language), None)

       if not stream_playlist or not audio_track:
           raise ValueError(f"No stream found for resolution {resolution} or language {language}")

       base_url = os.path.dirname(self.playlist_url)
       video_playlist_url = urljoin(base_url, stream_playlist.uri)
       audio_playlist_url = urljoin(base_url, audio_track.uri)

       video_playlist = m3u8.load(video_playlist_url)
       audio_playlist = m3u8.load(audio_playlist_url)

       video_segments_file = os.path.join(self.output_dir, 'video_segments.txt')
       audio_segments_file = os.path.join(self.output_dir, 'audio_segments.txt')
       output_file = os.path.join(self.output_dir, f'output_{resolution}_{language}.mp4')

       self._download_segments(video_playlist, video_segments_file, base_url=os.path.dirname(video_playlist_url), max_workers=max_workers)
       self._download_segments(audio_playlist, audio_segments_file, base_url=os.path.dirname(audio_playlist_url), max_workers=max_workers)
       self._combine_streams(video_segments_file, audio_segments_file, output_file)

       return output_file

   def _download_segments(self, playlist, segments_file, base_url, max_workers=5):
       with open(segments_file, 'w') as f, ThreadPoolExecutor(max_workers=max_workers) as executor:
           futures = []
           for segment in playlist.segments:
               segment_url = urljoin(base_url, segment.uri)
               output_path = os.path.join(self.output_dir, os.path.basename(segment.uri))
               futures.append(executor.submit(self._download_segment, segment_url, output_path))
           for future in as_completed(futures):
               segment_path = future.result()
               f.write(f"file '{segment_path}'\n")

   def _download_segment(self, url, output_path):
       if not os.path.exists(output_path):
           response = requests.get(url, timeout=10)
           with open(output_path, 'wb') as f:
               f.write(response.content)
       return output_path

   def _combine_streams(self, video_segments, audio_segments, output_file):
       try:
           ffmpeg.input(video_segments, format='concat', safe=0).output('intermediate_video.mp4', c='copy').overwrite_output().run()
           ffmpeg.input(audio_segments, format='concat', safe=0).output('intermediate_audio.m4a', c='copy').overwrite_output().run()
           ffmpeg.input('intermediate_video.mp4').input('intermediate_audio.m4a').output(output_file, c='copy').overwrite_output().run()
       finally:
           for file in ['intermediate_video.mp4', 'intermediate_audio.m4a']:
               if os.path.exists(file):
                   os.remove(file)

app = Flask(__name__)
app.config['DOWNLOAD_FOLDER'] = 'downloads'
os.makedirs(app.config['DOWNLOAD_FOLDER'], exist_ok=True)

@app.route('/', methods=['GET', 'POST'])
def index():
   if request.method == 'POST':
       playlist_url = request.form.get('playlist_url')
       resolution = request.form.get('resolution')
       language = request.form.get('language')
       try:
           download_manager = M3U8DownloadManager(playlist_url)
           available_streams = download_manager.get_available_streams()
           available_audio_tracks = download_manager.get_available_audio_tracks()
           if not available_streams or not available_audio_tracks:
               return "No streams or audio tracks found", 400
           resolution = resolution or available_streams[0]
           language = language or available_audio_tracks[0]
           output_file = download_manager.download_stream(resolution=resolution, language=language)
           return send_file(output_file, as_attachment=True, download_name=os.path.basename(output_file))
       except Exception as e:
           return f"Error downloading stream: {str(e)}", 400
   return render_template('index.html')

@app.route('/get_streams', methods=['POST'])
def get_streams():
   playlist_url = request.form.get('playlist_url')
   try:
       download_manager = M3U8DownloadManager(playlist_url)
       return jsonify({
           'streams': download_manager.get_available_streams(),
           'audio_tracks': download_manager.get_available_audio_tracks()
       })
   except Exception as e:
       return jsonify({'error': str(e)}), 400

if __name__ == '__main__':
   app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
