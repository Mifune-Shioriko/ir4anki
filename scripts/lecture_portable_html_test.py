import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from lecture_occlusion import safe_text
class PortableHTML(unittest.TestCase):
 def test_media_math_and_active_markup(self):
  result=safe_text('Formula $x^2$ and ![](/media/picture.png)\n\n<script>alert(1)</script><svg onload="evil"/>')
  self.assertIn('src="picture.png"',result)
  self.assertIn('\\(x^2\\)',result)
  self.assertNotIn('<script',result);self.assertNotIn('<svg',result)
 def test_code_dollars_remain_literal(self):
  result=safe_text('`$literal$`')
  self.assertIn('<code>$literal$</code>',result)
if __name__=='__main__':unittest.main()
