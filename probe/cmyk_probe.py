import io, time, sys
from PIL import Image, ImageCms
src = sys.argv[1]; out = sys.argv[2]
cmyk_profiles = {
 "JapanColor2001Coated": "/Library/Application Support/Adobe/Color/Profiles/Recommended/JapanColor2001Coated.icc",
 "CoatedFOGRA39": "/Library/Application Support/Adobe/Color/Profiles/Recommended/CoatedFOGRA39.icc",
 "USWebCoatedSWOP": "/Library/Application Support/Adobe/Color/Profiles/Recommended/USWebCoatedSWOP.icc",
}
im = Image.open(src); im.load()
icc = im.info.get("icc_profile")
inp = ImageCms.ImageCmsProfile(io.BytesIO(icc))
print("embedded profile:", ImageCms.getProfileDescription(inp).strip(), "mode", im.mode, im.size)
small = im.reduce(8)  # gamut check on 1/8 scale
srgb = ImageCms.createProfile("sRGB")
for name, path in cmyk_profiles.items():
    prof = ImageCms.ImageCmsProfile(path)
    # gamut check: pixels that fall outside CMYK gamut are marked with alarm color
    ImageCms.core.set_alarm_codes = None
    t = ImageCms.buildProofTransform(inp, srgb, prof, "RGB", "RGB",
        renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC, proofRenderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
        flags=ImageCms.Flags.GAMUTCHECK | ImageCms.Flags.SOFTPROOFING)
    g = ImageCms.applyTransform(small, t)
    # default alarm color in lcms2 is (0,255,255)? lcms2 default alarm = cyan-ish 0x7F00 per channel -> (127,255,255)? count exact-alarm pixels
    from collections import Counter
    px = g.getdata(); c = Counter(px); top = c.most_common(1)[0]
    # alarm pixels: lcms default alarm codes are 0x7F00,0x7F00,0x7F00 -> (127,127,127)
    oog = sum(1 for p in px if p == (127,127,127))
    print(f"{name}: out-of-gamut (rel.col) ~{100*oog/len(px):.1f}% of pixels")
    t0 = time.time()
    tr = ImageCms.buildTransform(inp, prof, "RGB", "CMYK", renderingIntent=ImageCms.Intent.PERCEPTUAL, flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
    cm = ImageCms.applyTransform(im, tr)
    dt = time.time() - t0
    cm.save(f"{out}/{name}.jpg", quality=95, icc_profile=prof.tobytes(), subsampling=0)
    cm.save(f"{out}/{name}.tif", compression="tiff_lzw", icc_profile=prof.tobytes())
    print(f"  converted full-res in {dt:.1f}s -> {name}.jpg/.tif")
