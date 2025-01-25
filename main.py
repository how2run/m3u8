import os
import requests
import m3u8
import ffmpeg
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename

class M3U8DownloadManager:
    def __init__(self, playlist_url):
        """
        Initialize the download manager with an M3U8 playlist URL
        
        :param playlist_url: URL of the M3U8 playlist
        """
        self.playlist_url = playlist_url
        self.output_dir = os.path.join('downloads', str(uuid.uuid4()))
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Parse the playlist
        self.playlist = self._parse_playlist()
        
    def _parse_playlist(self):
        """
        Parse the M3U8 playlist and extract stream and audio information
        
        :return: Parsed M3U8 playlist object
        """
        try:
            response = requests.get(self.playlist_url)
            response.raise_for_status()
            return m3u8.loads(response.text)
        except Exception as e:
            print(f"Error parsing playlist: {e}")
            return None
    
    def get_available_streams(self):
        """
        Get available stream qualities
        
        :return: List of available stream resolutions
        """
        if not self.playlist or not self.playlist.playlists:
            return []
        
        return [f"{pl.stream_info.resolution[0]}x{pl.stream_info.resolution[1]}" 
                for pl in self.playlist.playlists]
    
    def get_available_audio_tracks(self):
        """
        Get available audio tracks
        
        :return: List of available audio languages
        """
        if not self.playlist or not self.playlist.media:
            return []
        
        return [media.name for media in self.playlist.media if media.type == 'AUDIO']
    
    def download_stream(self, resolution=None, language=None, max_workers=5):
        """
        Download video stream with selected audio track
        
        :param resolution: Desired stream resolution
        :param language: Desired audio language
        :param max_workers: Maximum concurrent downloads
        """
        # If no resolution specified, use the first available
        available_streams = self.get_available_streams()
        if not resolution and available_streams:
            resolution = available_streams[0]
        
        # If no language specified, use the first available audio track
        available_audio_tracks = self.get_available_audio_tracks()
        if not language and available_audio_tracks:
            language = available_audio_tracks[0]
        
        # Find matching stream playlist
        stream_playlist = next(
            (pl for pl in self.playlist.playlists 
             if f"{pl.stream_info.resolution[0]}x{pl.stream_info.resolution[1]}" == resolution), 
            None
        )
        
        if not stream_playlist:
            raise ValueError(f"No stream found for resolution {resolution}")
        
        # Find matching audio track
        audio_track = next(
            (media for media in self.playlist.media 
             if media.type == 'AUDIO' and media.name == language), 
            None
        )
        
        if not audio_track:
            raise ValueError(f"No audio track found for language {language}")
        
        # Resolve full URLs
        base_url = os.path.dirname(self.playlist_url)
        video_playlist_url = urljoin(base_url, stream_playlist.uri)
        audio_playlist_url = urljoin(base_url, audio_track.uri)
        
        # Download video and audio playlists
        video_playlist = m3u8.load(video_playlist_url)
        audio_playlist = m3u8.load(audio_playlist_url)
        
        # Prepare output files
        video_segments_file = os.path.join(self.output_dir, 'video_segments.txt')
        audio_segments_file = os.path.join(self.output_dir, 'audio_segments.txt')
        output_file = os.path.join(self.output_dir, f'output_{resolution}_{language}.mp4')
        
        # Download video segments
        self._download_segments(
            video_playlist, 
            video_segments_file, 
            base_url=os.path.dirname(video_playlist_url),
            max_workers=max_workers
        )
        
        # Download audio segments
        self._download_segments(
            audio_playlist, 
            audio_segments_file, 
            base_url=os.path.dirname(audio_playlist_url),
            max_workers=max_workers
        )
        
        # Combine segments using ffmpeg
        self._combine_streams(
            video_segments_file, 
            audio_segments_file, 
            output_file
        )
        
        return output_file
    
    def _download_segments(self, playlist, segments_file, base_url, max_workers=5):
        """
        Download segments concurrently
        
        :param playlist: M3U8 playlist object
        :param segments_file: File to store downloaded segment paths
        :param base_url: Base URL for resolving segment URLs
        :param max_workers: Maximum concurrent downloads
        """
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
        """
        Download individual segment
        
        :param url: Segment URL
        :param output_path: Local path to save segment
        :return: Path of downloaded segment
        """
        if not os.path.exists(output_path):
            response = requests.get(url)
            with open(output_path, 'wb') as f:
                f.write(response.content)
        return output_path
    
    def _combine_streams(self, video_segments, audio_segments, output_file):
        """
        Combine video and audio streams using ffmpeg
        
        :param video_segments: File with video segment list
        :param audio_segments: File with audio segment list
        :param output_file: Final output file path
        """
        try:
            # Combine video segments
            (
                ffmpeg
                .input(video_segments, format='concat', safe=0)
                .output('intermediate_video.mp4', c='copy')
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )
            
            # Combine audio segments
            (
                ffmpeg
                .input(audio_segments, format='concat', safe=0)
                .output('intermediate_audio.m4a', c='copy')
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )
            
            # Merge video and audio
            (
                ffmpeg
                .input('intermediate_video.mp4')
                .input('intermediate_audio.m4a')
                .output(output_file, c='copy')
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )
        finally:
            # Clean up intermediate files
            for file in ['intermediate_video.mp4', 'intermediate_audio.m4a']:
                if os.path.exists(file):
                    os.remove(file)

# Flask Web Application
app = Flask(__name__)
app.config['DOWNLOAD_FOLDER'] = 'downloads'
os.makedirs(app.config['DOWNLOAD_FOLDER'], exist_ok=True)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # Get form data
        playlist_url = request.form.get('playlist_url')
        resolution = request.form.get('resolution')
        language = request.form.get('language')
        
        try:
            # Initialize download manager
            download_manager = M3U8DownloadManager(playlist_url)
            
            # Get available streams and audio tracks
            available_streams = download_manager.get_available_streams()
            available_audio_tracks = download_manager.get_available_audio_tracks()
            
            # If no specific resolution or language provided, use first available
            resolution = resolution or (available_streams[0] if available_streams else None)
            language = language or (available_audio_tracks[0] if available_audio_tracks else None)
            
            # Download the stream
            output_file = download_manager.download_stream(
                resolution=resolution, 
                language=language
            )
            
            # Send the file for download
            return send_file(
                output_file, 
                as_attachment=True, 
                download_name=os.path.basename(output_file)
            )
        
        except Exception as e:
            return f"Error downloading stream: {str(e)}", 400
    
    return render_template('index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
