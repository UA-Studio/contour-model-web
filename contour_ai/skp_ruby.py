"""스케치업 확장(.rbz)과 그 안의 Ruby 코드, 사용 안내 글."""
import zipfile



# ---------------------------------------------------------------- 스케치업 확장(.rbz)
RBZ_NAME = "스케치업_확장_설치파일(모델아님).rbz"


HOWTO = """스케치업에서 콘타 모델 여는 법

이 폴더의 .rbz 파일은 모델이 아니라 '불러오기 확장'을 설치하는 파일입니다(처음 한 번만).
스케치업 모델은 model_sketchup.dae 에 들어 있고, 확장으로 불러오면 같은 폴더에 model_sketchup.skp 가 저장됩니다.

[처음 한 번] 확장 설치
  스케치업 > 확장 프로그램(Extensions) > Extension Manager > Install Extension
  > 스케치업_확장_설치파일(모델아님).rbz 선택

[매번] 모델 불러오기
  확장 프로그램 > 콘타 모델 > 콘타 모델 불러오기 (.dae → .skp) > model_sketchup.dae 선택
  - 레이어 구조대로 태그(지형 / 길 / 건물 > 05층처럼 층수별 / 나무)가 붙고, 건물마다 수치지도 정보가 들어갑니다.
  - 대지·경사면·곡면 위 길의 삼각형 모서리는 부드럽게 숨깁니다.
  - 같은 폴더에 model_sketchup.skp 로 저장됩니다.

확장 없이 열려면: 파일 > 가져오기(Import) > COLLADA(*.dae) > model_sketchup.dae
  (이때는 태그·건물 정보가 붙지 않고, 대지의 삼각형 모서리가 모두 선으로 보입니다)
"""


LOADER_RB = '''# encoding: UTF-8
require 'sketchup.rb'
require 'extensions.rb'

module ContourAI
  EXT = SketchupExtension.new('콘타 모델 불러오기', 'contour_ai_importer/main')
  EXT.description = 'contour_ai 로 만든 model_sketchup.dae 를 태그·건물 정보와 함께 불러와 .skp 로 저장'
  EXT.version = '1.0'
  EXT.creator = 'contour_ai'
  Sketchup.register_extension(EXT, true)
end
'''


MAIN_RB = '''# encoding: UTF-8
require 'json'
require 'contour_ai_importer/builder'

module ContourAI
  def self.entity_name(e)
    n = e.name.to_s
    n = e.definition.name.to_s if n.empty? && e.respond_to?(:definition)
    n.sub(/#\\d+\\z/, '').gsub('__', '::')
  end

  def self.tag_for(model, path)
    parts = path.split('::')
    name = parts.join(' > ')
    tag = model.layers[name] || model.layers.add(name)
    if parts.size > 1 && model.layers.respond_to?(:add_folder)
      begin
        folder = model.layers.folders.find { |f| f.name == parts[0] } || model.layers.add_folder(parts[0])
        tag.folder = folder
      rescue StandardError
      end
    end
    tag
  end

  def self.children(e)
    ents = e.respond_to?(:definition) ? e.definition.entities : e.entities
    ents.select { |c| c.is_a?(Sketchup::Group) || c.is_a?(Sketchup::ComponentInstance) }
  end

  # 곡면(대지·경사매스·곡면 위 길·나무)의 삼각형 모서리를 부드럽게 숨긴다. 꺾인 모서리(20° 넘게)는 그대로.
  def self.soften(ents, depth = 0)
    return if depth > 4
    ents.each do |e|
      if e.is_a?(Sketchup::Edge)
        f = e.faces
        if f.size == 2 && f[0].normal.angle_between(f[1].normal) < 20.degrees
          e.soft = true
          e.smooth = true
        end
      elsif e.is_a?(Sketchup::Group)
        soften(e.entities, depth + 1)
      elsif e.is_a?(Sketchup::ComponentInstance)
        soften(e.definition.entities, depth + 1)
      end
    end
  end

  def self.import_model
    path = UI.openpanel('콘타 모델 선택 (model_sketchup.dae)', '', 'COLLADA|*.dae||')
    return unless path
    model = Sketchup.active_model
    info_path = File.join(File.dirname(path), 'model_sketchup_info.json')
    info = File.exist?(info_path) ? JSON.parse(File.read(info_path, encoding: 'UTF-8')) : {}
    model.start_operation('콘타 모델 불러오기', true)
    defn = model.definitions.import(path)
    unless defn
      model.abort_operation
      UI.messagebox('불러오지 못했습니다: ' + path)
      return
    end
    inst = model.active_entities.add_instance(defn, Geom::Transformation.new)
    tops = inst.explode.select { |e| e.is_a?(Sketchup::Group) || e.is_a?(Sketchup::ComponentInstance) }
    n_bld = 0
    tops.each do |top|
      layer_path = entity_name(top)
      next if layer_path.empty?
      top.name = layer_path.split('::').join(' > ')
      top.layer = tag_for(model, layer_path)
      if layer_path.start_with?('지형') || layer_path.include?('곡면대지') || layer_path.start_with?('나무')
        soften(top.respond_to?(:definition) ? top.definition.entities : top.entities)
      end
      next unless layer_path.start_with?('건물')
      children(top).each do |b|
        key = entity_name(b).split('_').first
        b.name = entity_name(b).tr('_', ' ').strip
        attrs = info[key]
        next unless attrs
        attrs.each { |k, v| b.set_attribute('수치지도', k, v) }
        n_bld += 1
      end
    end
    model.commit_operation
    skp = path.sub(/\\.dae\\z/i, '.skp')
    model.save(skp)
    UI.messagebox("불러왔습니다. 태그 #{tops.size}개, 건물 정보 #{n_bld}동.\\n저장: #{skp}\\n" \\
                  "건물을 선택하고 '엔터티 정보' 또는 확장의 속성 보기로 수치지도 정보를 볼 수 있습니다.")
  end

  def self.show_info
    e = Sketchup.active_model.selection.first
    return UI.messagebox('건물 하나를 선택하세요.') unless e
    d = e.attribute_dictionary('수치지도')
    return UI.messagebox('수치지도 정보가 없는 개체입니다.') unless d
    UI.messagebox(d.map { |k, v| "#{k}: #{v}" }.join("\\n"))
  end

  unless file_loaded?(__FILE__)
    menu = UI.menu('Extensions').add_submenu('콘타 모델')
    menu.add_item('콘타 모델 만들기 (.json → .skp, 편집용 면)') do
      path = UI.openpanel('model_sketchup_build.json 선택', '', 'JSON|*.json||')
      if path
        build_from(path, false)
        log = path.sub(/\\.json\\z/i, '.log')
        UI.messagebox(File.exist?(log) ? File.read(log, encoding: 'UTF-8') : '끝났습니다')
      end
    end
    menu.add_item('콘타 모델 불러오기 (.dae → .skp)') { import_model }
    menu.add_item('선택한 건물 정보 보기') { show_info }
    file_loaded(__FILE__)
  end
end
'''


def write_rbz(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("contour_ai_importer.rb", LOADER_RB.encode("utf-8"))
        z.writestr("contour_ai_importer/main.rb", MAIN_RB.encode("utf-8"))
        z.writestr("contour_ai_importer/builder.rb", BUILDER_RB.encode("utf-8"))
    return path


# ---------------------------------------------------------------- 스케치업 안에서 진짜 면으로 만들기
BUILDER_RB = '''# encoding: UTF-8
# contour_ai: model_sketchup_build.json 을 읽어 스케치업 안에서 '진짜 면'으로 모델을 만들고 .skp(2021 형식)로 저장한다.
# 판·건물은 바닥 면을 그리고 밀어 올려(Push/Pull) 만든다 → 삼각형으로 쪼개지지 않아 그대로 편집할 수 있다.
require 'json'

module ContourAI
  M = 39.37007874015748   # m → 스케치업 내부 단위(inch)

  def self.tag_for(model, path)
    @tags ||= {}
    return @tags[path] if @tags[path] && @tags[path].valid?
    parts = path.split('::')
    tag = model.layers[parts.join(' > ')] || model.layers.add(parts.join(' > '))
    if parts.size > 1 && model.layers.respond_to?(:add_folder)
      begin
        folder = model.layers.folders.find { |f| f.name == parts[0] } || model.layers.add_folder(parts[0])
        tag.folder = folder
      rescue StandardError
      end
    end
    @tags[path] = tag
  end

  def self.mat_for(model, rgb, name, alpha = 1.0)
    key = (name && !name.to_s.empty?) ? name.to_s : "색 #{rgb.join(',')}"
    key = "#{key} #{(alpha * 100).round}%" if alpha && alpha < 0.999
    @mats ||= {}
    return @mats[key] if @mats[key] && @mats[key].valid?
    m = model.materials[key]
    unless m
      m = model.materials.add(key)
      m.color = Sketchup::Color.new(*rgb)
      m.alpha = alpha if alpha && alpha < 0.999
    end
    @mats[key] = m
  end

  def self.pts(flat, z)
    flat.each_slice(2).map { |x, y| Geom::Point3d.new(x * M, y * M, z * M) }
  end

  # 바닥 다각형(구멍 포함) → 면 → 밀어 올리기. 그룹 하나로.
  def self.add_ext(ents, it, mat)
    g = ents.add_group
    e = g.entities
    loops = it['loops']
    face = e.add_face(pts(loops[0], it['z0']))
    unless face
      g.erase!
      @fails += 1
      return nil
    end
    loops[1..-1].each do |h|
      begin
        hf = e.add_face(pts(h, it['z0']))
        hf.erase! if hf && hf.valid? && hf != face
      rescue ArgumentError
      end
    end
    face = e.grep(Sketchup::Face).max_by(&:area) unless face.valid?
    face.reverse! if face.normal.z < 0
    face.pushpull((it['z1'] - it['z0']) * M)
    g.material = mat
    @n_ext += 1
    g
  rescue ArgumentError, TypeError
    @fails += 1
    g.erase! if g && g.valid?
    nil
  end

  def self.fill_mesh(ents, it, mat)
    v = it['v']
    f = it['f']
    pm = Geom::PolygonMesh.new(v.size / 3, f.size / 3)
    idx = []
    v.each_slice(3) { |x, y, z| idx << pm.add_point(Geom::Point3d.new(x * M, y * M, z * M)) }
    f.each_slice(3) do |a, b, c|
      next if idx[a] == idx[b] || idx[b] == idx[c] || idx[a] == idx[c]
      begin
        pm.add_polygon(idx[a], idx[b], idx[c])
      rescue ArgumentError
      end
    end
    flags = it['soft'] ? 12 : 0   # 곡면: 모서리 부드럽게(AUTO_SOFTEN + SMOOTH_SOFT_EDGES)
    if ents.respond_to?(:fill_from_mesh)
      ents.fill_from_mesh(pm, true, flags, mat, mat)
    else
      ents.add_faces_from_mesh(pm, flags, mat, mat)
    end
    merge_coplanar(ents) unless it['soft']
  end

  def self.add_mesh(ents, it, mat)
    g = ents.add_group
    fill_mesh(g.entities, it, mat)
    @n_mesh += 1
    g
  end

  # 같은 평면·같은 재료로 이어진 삼각형 사이 선을 지워 한 면으로 (편집하기 쉽게)
  def self.merge_coplanar(ents)
    ents.grep(Sketchup::Edge).each do |e|
      next unless e.valid?
      fs = e.faces
      next unless fs.size == 2
      n0 = fs[0].normal
      n1 = fs[1].normal
      next unless n0.samedirection?(n1)
      next unless fs[0].material == fs[1].material
      e.erase!
    end
  end

  def self.build_from(json_path, quit = false)
    t0 = Time.now
    log_path = json_path.sub(/\\.json\\z/i, '.log')
    skp = File.join(File.dirname(json_path), 'model_sketchup.skp')
    @fails = 0
    @n_ext = 0
    @n_mesh = 0
    @tags = {}
    @mats = {}
    begin
      data = JSON.parse(File.read(json_path, encoding: 'UTF-8'))
      model = Sketchup.active_model
      model.start_operation('콘타 모델 만들기', true)
      begin
        model.options['UnitsOptions']['LengthUnit'] = 4      # 미터
        model.options['UnitsOptions']['LengthPrecision'] = 3
      rescue StandardError
      end
      tops = {}
      top_for = lambda do |tag|
        tops[tag] ||= begin
          t = model.entities.add_group
          t.name = tag.split('::').join(' > ')
          t.layer = tag_for(model, tag)
          t
        end
      end
      n_grp = 0
      data['groups'].each do |gd|
        top = top_for.call(gd['tag'])
        g = top.entities.add_group
        g.name = gd['name'].to_s
        (gd['attrs'] || {}).each { |k, v| g.set_attribute('수치지도', k, v) }
        gd['items'].each do |it|
          mat = mat_for(model, it['rgb'], it['mat'], it['alpha'] || 1.0)
          it['t'] == 'ext' ? add_ext(g.entities, it, mat) : add_mesh(g.entities, it, mat)
        end
        g.erase! if g.entities.length == 0
        n_grp += 1
      end
      defs = {}
      (data['defs'] || {}).each do |id, d|
        cd = model.definitions.add(d['name'].to_s)
        d['meshes'].each { |mm| fill_mesh(cd.entities, mm.merge('soft' => true), mat_for(model, mm['rgb'], mm['mat'])) }
        defs[id] = cd
      end
      n_inst = 0
      (data['instances'] || []).each do |tag, id, m|
        cd = defs[id]
        next unless cd
        a = m.dup
        a[12] *= M
        a[13] *= M
        a[14] *= M
        top_for.call(tag).entities.add_instance(cd, Geom::Transformation.new(a))
        n_inst += 1
      end
      (data['lines'] || []).each do |ln|
        p = ln['p'].each_slice(3).map { |x, y, z| Geom::Point3d.new(x * M, y * M, z * M) }
        top_for.call(ln['tag']).entities.add_curve(p) if p.size >= 2
      end
      model.commit_operation
      Sketchup.active_model.active_view.zoom_extents rescue nil
      ver = nil
      %w[VERSION_2021 VERSION_2020 VERSION_2019 VERSION_2018 VERSION_2017].each do |c|
        if Sketchup::Model.const_defined?(c)
          ver = Sketchup::Model.const_get(c)
          break
        end
      end
      ok = ver ? model.save(skp, ver) : model.save(skp)
      File.write(log_path, "#{ok ? 'ok' : 'save-failed'}\\t#{skp}\\tgroups=#{n_grp}\\text=#{@n_ext}\\tmesh=#{@n_mesh}\\t" \\
                           "instances=#{n_inst}\\tfails=#{@fails}\\tseconds=#{(Time.now - t0).round}\\n")
    rescue StandardError => ex
      File.write(log_path, "error\\t#{ex.class}: #{ex.message}\\n#{(ex.backtrace || [])[0, 8].join("\\n")}\\n") rescue nil
      UI.messagebox("콘타 모델을 만들지 못했습니다: #{ex.message}") unless quit
    end
    Sketchup.quit if quit
  end
end
'''
