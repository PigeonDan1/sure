#!/usr/bin/env python3
"""
Test script for DiariZen model.
Tests the speaker diarization functionality with sample audio.
"""

import sys
from pathlib import Path

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

try:
    from model import DiariZenModel
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Note: Make sure you're in the 'diarizen' conda environment")
    sys.exit(1)


def test_model_initialization():
    """Test that the model can be initialized."""
    print("Testing model initialization...")
    try:
        model = DiariZenModel()
        print("✓ Model initialized successfully")
        return True, model
    except Exception as e:
        print(f"❌ Failed to initialize model: {e}")
        return False, None


def test_diarization_with_example(model, example_path):
    """Test diarization on example audio."""
    print(f"\nTesting diarization on {example_path.name}...")
    try:
        result = model.diarize(str(example_path))
        
        print(f"✓ Diarization completed")
        print(f"  Detected {result.num_speakers} speakers")
        print(f"  {len(result.segments)} segments")
        
        print("\n  Segments:")
        for i, seg in enumerate(result.segments[:10]):  # Show first 10
            print(f"    {seg.start:.1f}s - {seg.end:.1f}s: {seg.speaker}")
        
        if len(result.segments) > 10:
            print(f"    ... and {len(result.segments) - 10} more segments")
        
        # Check RTTM output
        rttm = result.to_rttm(example_path.stem)
        if rttm:
            print(f"\n✓ RTTM output generated ({len(rttm.split(chr(10)))} lines)")
        
        return True
    except Exception as e:
        print(f"❌ Diarization failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_rttm_save(model, example_path):
    """Test saving RTTM to file."""
    print(f"\nTesting RTTM file save...")
    try:
        output_dir = Path(__file__).parent / "test_results"
        output_dir.mkdir(exist_ok=True)
        
        rttm_path = model.diarize_with_rttm_output(
            str(example_path),
            output_dir=str(output_dir)
        )
        
        if Path(rttm_path).exists():
            print(f"✓ RTTM file saved to: {rttm_path}")
            # Read and display
            content = Path(rttm_path).read_text()
            print("\n  RTTM content preview:")
            for line in content.split('\n')[:5]:
                print(f"    {line[:100]}...")
            return True
        else:
            print(f"❌ RTTM file not found at expected location")
            return False
    except Exception as e:
        print(f"❌ Failed to save RTTM: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("DiariZen Model Test")
    print("=" * 60)
    
    # Check for example audio
    example_path = Path(__file__).parent / "diarizen_src" / "example" / "EN2002a_30s.wav"
    if not example_path.exists():
        print(f"\n⚠ Example audio not found at {example_path}")
        print("Using any available .wav file in current directory...")
        
        wav_files = list(Path(__file__).parent.glob("*.wav"))
        if wav_files:
            example_path = wav_files[0]
            print(f"Using: {example_path}")
        else:
            print("❌ No .wav files found. Please provide test audio.")
            sys.exit(1)
    
    # Test 1: Initialization
    success, model = test_model_initialization()
    if not success:
        print("\n❌ Model initialization failed, cannot continue tests")
        sys.exit(1)
    
    # Test 2: Diarization
    success = test_diarization_with_example(model, example_path)
    if not success:
        print("\n❌ Diarization test failed")
        sys.exit(1)
    
    # Test 3: RTTM save
    success = test_rttm_save(model, example_path)
    if not success:
        print("\n⚠ RTTM save test failed (but diarization works)")
    
    print("\n" + "=" * 60)
    print("All critical tests passed! ✓")
    print("=" * 60)
    print("\nModel is ready for SURE evaluation.")


if __name__ == "__main__":
    main()
