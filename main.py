import os
import requests
import m3u8
import ffmpeg
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

class M3U8DownloadManager:
    def __init__(self, playlist_url, output_dir='downloads'):
        """
        Initialize the download manager with an M3U8 playlist URL
        
        :param playlist_url: URL of the M3U8 playlist
        :param output_dir: Directory to save downloaded files
        """
        self.playlist_url = playlist_url
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        # Parse the playlist
        self.playlist = self._parse_playlist()
        
    def _parse_playlist(self):
        """
        Parse the M3U8 playlist and extract stream and audio information
        
        :return: Parsed M3U8 playlist object
        """
        response = requests.get(self.playlist_url)
        return m3u8.loads(response.text)
    
    def get_available_streams(self):
        """
        Get available stream qualities
        
        :return: List of available stream resolutions
        """
        return [f"{stream.stream_info.resolution[0]}x{stream.stream_info.resolution[1]}" 
                for stream in self.playlist.playlists]
    
    def get_available_audio_tracks(self):
        """
        Get available audio tracks
        
        :return: List of available audio languages
        """
        return [media.name for media in self.playlist.media if media.type == 'AUDIO']
    
    def download_stream(self, resolution='720p', language='Hindi', max_workers=5):
        """
        Download video stream with selected audio track
        
        :param resolution: Desired stream resolution
        :param language: Desired audio language
        :param max_workers: Maximum concurrent downloads
        """
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
        
        print(f"Download completed: {output_file}")
    
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

# Example usage
def main():
    playlist_url = "https://s10.nm-cdn2.top/files/0TI7NE8OHKMJJOUUSP339YB6SE.m3u8_in=unknown__pn"
    
    # Initialize download manager
    download_manager = M3U8DownloadManager(playlist_url)
    
    # Show available streams and audio tracks
    print("Available Streams:", download_manager.get_available_streams())
    print("Available Audio Tracks:", download_manager.get_available_audio_tracks())
    
    # Download specific stream and audio track
    download_manager.download_stream(resolution='720p', language='Hindi')

if __name__ == "__main__":
    main()
