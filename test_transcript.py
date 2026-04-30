import sys
import re

# Add path so python finds the module correctly
import sys
from youtube_transcript_api import YouTubeTranscriptApi

def test_extract(video_id):
    print(f"Testing extraction for video ID: {video_id}")
    try:
        transcript = YouTubeTranscriptApi().fetch(video_id, languages=['ko', 'en'])
        text = " ".join([t.text for t in transcript])
        print("Success! Extracted text preview:")
        print(text[:200] + "...")
    except Exception as e:
        print(f"Failed extraction: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_extract(sys.argv[1])
    else:
        test_extract("jNQXAC9IVRw") # Me at the zoo
