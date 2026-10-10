from perception_service.pipeline import CameraPipeline


def test_fps_is_zero_without_frames():
    assert CameraPipeline("rgb").measured_fps() == 0.0


def test_fps_from_timestamps():
    pipe = CameraPipeline("rgb")
    for i in range(31):
        pipe.on_frame(i / 30)
    assert abs(pipe.measured_fps() - 30.0) < 1e-6
