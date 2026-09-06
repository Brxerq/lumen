"""ASUS Aura laptop adapter (keyboard zones + light bar) and the old hook shim."""

from lumen.devices import asus_aura


def test_frame_report_layout():
    rep = asus_aura.frame_report([(1, 2, 3)] * 4, [(4, 5, 6)] * 2)
    assert len(rep) == 64
    assert rep[:9] == bytes([0x5D, 0xBC, 0x00, 0x01, 0x04, 0, 0, 0, 0])
    assert rep[9:21] == bytes([1, 2, 3]) * 4          # triplets 0-3: keyboard zones
    assert rep[21:27] == bytes([1, 2, 3]) * 2         # 4-5: padding with the last keyboard zone
    assert rep[27:33] == bytes([4, 5, 6]) * 2         # 6-7: light bar halves
    assert rep[33:63] == bytes([4, 5, 6]) * 10 and rep[63] == 0
    assert asus_aura.init_direct_report()[:2] == bytes([0x5D, 0xBC])
    assert asus_aura.brightness_report()[:5] == bytes([0x5D, 0xBA, 0xC5, 0xC4, 3])


def test_zone_lists_are_padded_and_clipped():
    rep = asus_aura.frame_report([(9, 9, 9)], [(7, 7, 7), (8, 8, 8), (1, 1, 1)])
    assert rep[9:21] == bytes([9, 9, 9]) * 4
    assert rep[27:33] == bytes([7, 7, 7]) + bytes([8, 8, 8])


def test_surfaces_share_one_frame():
    class FakeController(asus_aura.AuraController):
        def __init__(self):
            super().__init__(b"path", 0x19B6)
            self.sent = []

        def flush(self):
            self.sent.append(asus_aura.frame_report(self.keyboard, self.lightbar))

    c = FakeController()
    kb = asus_aura.AuraSurface(c, "keyboard", 4, "G513RM", True)
    lb = asus_aura.AuraSurface(c, "lightbar", 2, "G513RM", True)
    kb.set_zones([(1, 0, 0), (2, 0, 0), (3, 0, 0), (4, 0, 0)])
    lb.set_color((0, 0, 9))
    assert c.sent[-1][9:21] == bytes([1, 0, 0, 2, 0, 0, 3, 0, 0, 4, 0, 0])
    assert c.sent[-1][27:33] == bytes([0, 0, 9]) * 2
    assert kb.zone_count == 4 and "zones" in kb.capabilities and kb.kind == "keyboard" and lb.kind == "lightbar"
