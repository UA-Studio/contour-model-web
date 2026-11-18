# 스케치업 없이 BUILDER_RB(스케치업 만들기 스크립트)의 흐름을 돌려 보는 가짜 SketchUp API.
# 사용: ruby sketchup_mock.rb 빌더.rb model_sketchup_build.json
#   또는 가짜 SketchUp.exe 로: ruby sketchup_mock.rb -RubyStartup 실행스크립트.rb (test_make_skp.py 가 씀)   → 같은 폴더에 .log 와 model_sketchup.skp(가짜, 개수 기록)
$stats = Hash.new(0)
class Numeric; def degrees; self * Math::PI / 180; end; end
module Geom
  class Point3d; attr_reader :x, :y, :z; def initialize(x, y, z); @x, @y, @z = x, y, z; end; end
  class Vector3d
    attr_reader :x, :y, :z
    def initialize(x, y, z); @x, @y, @z = x, y, z; end
    def samedirection?(o); (x * o.x + y * o.y + z * o.z) > 0.9999; end
  end
  class Transformation; def initialize(a); raise ArgumentError, 'need 16' unless a.size == 16; end; end
  class PolygonMesh
    def initialize(np = 0, nf = 0); @pts = {}; @polys = 0; end
    def add_point(p); k = [p.x.round(6), p.y.round(6), p.z.round(6)]; @pts[k] ||= @pts.size + 1; end
    def add_polygon(*i); raise ArgumentError, 'dup' if i.uniq.size < 3; @polys += 1; end
    attr_reader :polys
  end
end
module Sketchup
  class Entity; def valid?; !@erased; end; def erase!; @erased = true; end; end
  class Face < Entity
    def initialize(pts); @pts = pts; zs = pts.map(&:z); @z = zs.first; raise ArgumentError, 'not planar' unless zs.uniq.size == 1
      raise ArgumentError, 'few' if pts.size < 3
      a = 0; pts.each_with_index { |p, i| q = pts[(i + 1) % pts.size]; a += p.x * q.y - q.x * p.y }; @a = a / 2.0
      raise ArgumentError, 'degenerate' if @a.abs < 1e-6
    end
    def area; @a.abs; end
    def normal; Geom::Vector3d.new(0, 0, (@z.abs < 1e-9 ? -1 : (@a > 0 ? 1 : -1))); end
    def reverse!; @a = -@a; @z = 1 if @z.abs < 1e-9; self; end
    def pushpull(d); raise ArgumentError, 'zero' if d.abs < 1e-9; $stats[:pushpull] += 1; end
    attr_accessor :material
  end
  class Edge < Entity; def faces; []; end; end
  class Entities
    include Enumerable
    def initialize; @a = []; end
    def each(&b); @a.each(&b); end
    def length; @a.count(&:valid?); end
    def add_group; g = Group.new; @a << g; $stats[:groups] += 1; g; end
    def add_face(pts); f = Face.new(pts); @a << f; $stats[:faces] += 1; f; end
    def fill_from_mesh(pm, w, flags, m1, m2); $stats[:mesh_polys] += pm.polys; @a << Face.new([Geom::Point3d.new(0,0,1),Geom::Point3d.new(1,0,1),Geom::Point3d.new(0,1,1)]); end
    def add_instance(d, t); $stats[:instances] += 1; @a << Entity.new; end
    def add_curve(p); $stats[:curves] += 1; end
  end
  class Group < Entity
    attr_accessor :name, :layer, :material
    def entities; @e ||= Entities.new; end
    def set_attribute(d, k, v); $stats[:attrs] += 1; end
  end
  class Definition; attr_reader :name; def initialize(n); @name = n; end; def entities; @e ||= Entities.new; end; end
  class Color; def initialize(*a); end; end
  class Material; attr_accessor :color, :alpha; def valid?; true; end; end
  class Model
    VERSION_2021 = 21
    def entities; @e ||= Entities.new; end
    def layers; @l ||= Layers.new; end
    def materials; @m ||= Materials.new; end
    def definitions; @d ||= Defs.new; end
    def options; Hash.new { |h, k| h[k] = {} }; end
    def start_operation(*a); end
    def commit_operation; end
    def active_view; nil; end
    def save(path, ver = nil); File.write(path, "mock skp v#{ver} #{$stats.to_a.inspect}"); true; end
  end
  class Layers < Hash
    def [](n); super(n); end
    def add(n); self[n] = Entity.new; end
    def folders; @f ||= []; end
    def add_folder(n); f = Struct.new(:name).new(n); folders << f; f; end
  end
  class Materials < Hash; def add(n); self[n] = Material.new; end; end
  class Defs; def add(n); $stats[:defs] += 1; Definition.new(n); end; end
  def self.active_model; @m ||= Model.new; end
  def self.quit; $stats[:quit] += 1; end
end
module UI; def self.messagebox(m); puts "MSG #{m}"; end; def self.start_timer(*a); yield; end; end
class Sketchup::Entity; def folder=(f); end; end
if ARGV[0] == '-RubyStartup'      # 가짜 SketchUp.exe 로 쓸 때: 프로그램이 만든 실행 스크립트를 그대로 돌린다
  load ARGV[1]
else
  load ARGV[0]
  ContourAI.build_from(ARGV[1], true)
  puts File.read(ARGV[1].sub(/\.json\z/i, '.log'))
end
puts $stats.inspect
